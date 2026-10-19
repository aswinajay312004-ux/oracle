"""The Database screen's order view: any order in Oracle (also one entered in Axpert) read as the same order sheet as the
Saved screen, and for an order POPPYS saved, each value compared with what the Excel file gave (the save receipt).

Tables and columns come from the write mapping (level, business field, parent link, master), never from names here.
``fetch(table, columns, where_column, value, limit) -> [row dict]`` and ``names(master_table, key, label, ids) -> {id: name}``
run the read-only queries; this module only shapes and compares what they return."""
from __future__ import annotations
import re
from . import readback

MAX_LINES = 500
MAX_SIZES = 5000
LIST_FIELDS = dict(order_ref='order.order_ref', date='order.order_date', buyer='order.buyer', style='order.style',
                   description='order.description', po='order.po_number', pieces='calc.pieces')


def tables(mapping):
    """(order header, line, size) tables of the write mapping."""
    level = {spec.get('level'): t for t, spec in mapping['tables'].items() if spec.get('level') in ('order', 'line', 'size')}
    return level.get('order'), level.get('line'), level.get('size')


def column(mapping, table, field=None, parent=None):
    cols = (mapping['tables'].get(table) or {}).get('columns', {})
    return next((c for c, x in cols.items() if (field and x.get('field') == field) or (parent and x.get('parent') == parent)), None)


def list_columns(mapping, present):
    """{name: column} of the order header shown in the order list; columns missing on this database are left out."""
    head = tables(mapping)[0]
    out = {k: column(mapping, head, f) for k, f in LIST_FIELDS.items()}
    return {k: c for k, c in out.items() if c and c in present}


def master_of(mapping, table):
    """{column: (master table, key, label)} for the master references of a mapped table."""
    spec = mapping['tables'].get(table)
    if not spec:
        return {}
    by_table = {m['table']: m for m in mapping['masters'].values()}
    return {c: (t, by_table[t]['key'], by_table[t]['label']) for c, t in readback._refs(mapping, spec).items() if t in by_table}


def decode(mapping, rows_by_table, names):
    """{table: {column: {id: name}}} for every master reference in the rows (one query per master table)."""
    wanted = {}
    for table, rows in rows_by_table.items():
        for c, (mt, key, label) in master_of(mapping, table).items():
            ids = {r.get(c) for r in rows if isinstance(r.get(c), (int, float))}
            if ids:
                wanted.setdefault((mt, key, label), set()).update(ids)
    found = {mt: names(mt, key, label, sorted(ids)[:1000]) for (mt, key, label), ids in wanted.items()}
    return {table: {c: found.get(mt, {}) for c, (mt, _, _) in master_of(mapping, table).items()} for table in rows_by_table}


def _node(table, key, rows, labels, kind):
    return dict(table=table, key=key, kind=kind, records=[
        dict(pk=r.get(key), kind='written', found=True, matches=True, mismatched=[], live=r,
             written_columns=sorted(c for c, v in r.items() if v is not None and v != ''),
             decoded={c: m[r[c]] for c, m in labels.get(table, {}).items() if r.get(c) in m}) for r in rows])


def nodes(mapping, head_rows, line_rows, size_rows, labels):
    head, line, size = tables(mapping)
    spec = mapping['tables']
    return [_node(head, spec[head]['key'], head_rows, labels, 'header'),
            *([_node(line, spec[line]['key'], line_rows, labels, 'detail')] if line else []),
            *([_node(size, spec[size]['key'], size_rows, labels, 'detail')] if size else [])]


def read(mapping, present, fetch, ordmasid):
    """The order as Oracle has it now: (header row or None, its line rows, its size rows)."""
    head, line, size = tables(mapping)
    spec = mapping['tables']
    cols = lambda t: [c for c in spec[t]['columns'] if c in present(t)]
    found = fetch(head, cols(head), spec[head]['key'], ordmasid, 1)
    if not found:
        return None, [], []
    lines = fetch(line, cols(line), column(mapping, line, parent=head), ordmasid, MAX_LINES) if line and column(mapping, line, parent=head) else []
    sizes = fetch(size, cols(size), column(mapping, size, parent=head), ordmasid, MAX_SIZES) if size and column(mapping, size, parent=head) else []
    return found[0], lines, sizes


def receipt_rows(mapping, receipt, ordmasid):
    """The rows a POPPYS save wrote for this order (header, its lines and its size rows), from the save receipt."""
    head, line, size = tables(mapping)
    spec = mapping['tables']
    rows = [r for r in receipt.get('rows', []) if r['table'] in (head, line, size)]
    pick = lambda t, c: [dict(r['values'], **{spec[t]['key']: r.get('pk', r['values'].get(spec[t]['key']))})
                         for r in rows if r['table'] == t and c and r['values'].get(c) == ordmasid]
    return (pick(head, spec[head]['key']), pick(line, column(mapping, line, parent=head)) if line else [],
            pick(size, column(mapping, size, parent=head)) if size else [])


_MIDNIGHT = re.compile(r'^(\d{4}-\d{2}-\d{2})[ T]00:00(:00)?$')


def _value(v):
    """A date saved at midnight reads the same as the plain date."""
    return _MIDNIGHT.sub(r'\1', v) if isinstance(v, str) else v


def _key(v):
    return '' if v is None else str(v).strip().upper()


def compare(oracle_sheet, excel_sheet):
    """Each value Oracle holds against what the Excel file gave. Lines are matched by colour and ship date, sizes by
    name. Differing cells are listed (and marked on the Oracle sheet's lines as ``excel``)."""
    excel_head = {f['label']: f['value'] for f in excel_sheet['header']}
    oracle_head = {f['label']: f['value'] for f in oracle_sheet['header']}
    details = [dict(label=label, excel=v, oracle=oracle_head.get(label)) for label, v in excel_head.items()]
    details_diff = [d for d in details if not readback._same(_value(d['oracle']), _value(d['excel']))]
    by_line = {(_key(l['colour']), _key(l['ship_date'])): l for l in excel_sheet['lines']}
    cells = 0; cells_diff = []; seen = set()
    for l in oracle_sheet['lines']:
        k = (_key(l['colour']), _key(l['ship_date'])); x = by_line.get(k)
        if x is None:
            l['extra'] = True; continue
        seen.add(k); l['excel'] = {}
        for s in dict.fromkeys([*x['sizes'], *l['sizes']]):
            cells += 1
            if not readback._same(l['sizes'].get(s), x['sizes'].get(s)):
                l['excel'][s] = x['sizes'].get(s)
                cells_diff.append(dict(colour=l['colour'], size=s, excel=x['sizes'].get(s), oracle=l['sizes'].get(s)))
    missing = [x['colour'] for k, x in by_line.items() if k not in seen]
    extra = [l['colour'] for l in oracle_sheet['lines'] if l.get('extra')]
    pieces = dict(excel=excel_sheet['total'], oracle=oracle_sheet['total'])
    same = not details_diff and not cells_diff and not missing and not extra and readback._same(pieces['oracle'], pieces['excel'])
    return dict(same=same, pieces=pieces, details=dict(count=len(details), different=details_diff),
                cells=dict(count=cells, different=cells_diff), missing_lines=missing, extra_lines=extra,
                lines=dict(excel=len(excel_sheet['lines']), oracle=len(oracle_sheet['lines'])))


def _span(cells):
    cells = [c for c in cells if c]
    if not cells:
        return None
    return cells[0] if len(cells) == 1 or cells[0] == cells[-1] else f'{cells[0]}–{cells[-1]}'


def _rows_of(value):
    """'rows 31–36': the Excel rows the cells inside a list of read items (fabric, trims, lines) come from."""
    rows = set()

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get('cell'), str) and (m := re.fullmatch(r'[A-Z]{1,3}(\d+)', v['cell'])):
                rows.add(int(m.group(1)))
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return None if not rows else f'row {min(rows)}' if len(rows) == 1 else f'rows {min(rows)}–{max(rows)}'


def excel_map(mapping, order, sheet, receipt, check, automatic=False):
    """Where each value of the Excel file is now, for a person to tick off against the file: from_file (Excel cell, the
    value there, the value Oracle holds now), filled (values POPPYS put in that are not in the file, with why) and kept
    (values read from the file that Oracle does not hold, with why). Built from the draft's cell provenance.
    state: 'same' (Oracle holds the Excel value), 'changed' (changed before saving: edited on the review screen, or
    matched to an existing name) or 'different' (Oracle no longer holds what was saved)."""
    def state(excel, now, saved_ok):
        if not saved_ok:
            return 'different'
        return 'same' if readback._same(_value(_key(now)), _value(_key(excel))) or _key(now) == _key(excel) else 'changed'

    head, line, size = tables(mapping)
    prov = order.get('provenance') or {}
    differs = {d['label'] for d in (check or {}).get('details', {}).get('different', [])}
    oracle = {f['label']: f['value'] for f in sheet['header']}
    from_file, filled = [], []
    for c, x in mapping['tables'][head]['columns'].items():
        label = x.get('label') or c
        if x.get('key') or label not in oracle:
            continue
        field = x.get('field') or ''
        if field == 'calc.pieces':
            continue                                    # the pieces are the size table, listed below
        p = prov.get(field.split('.', 1)[1]) if field.startswith('order.') else None
        if p and p.get('cell'):
            from_file.append(dict(label=label, cell=p['cell'], excel=p.get('value'), oracle=oracle[label],
                                  state=state(p.get('value'), oracle[label], label not in differs)))
        else:
            why = (p or {}).get('derivation') or (('Next number of the OC series' if automatic else 'Typed in POPPYS')
                                                  if field == 'order.order_ref' else 'Company default')
            filled.append(dict(label=label, value=oracle[label], why=why))
    ship = prov.get('ship_date') or {}
    ships = {l['ship_date'] for l in sheet['lines'] if l.get('ship_date')}
    if ship.get('cell') and ships:
        now = next(iter(ships)) if len(ships) == 1 else ', '.join(sorted(ships))
        from_file.append(dict(label='Ship date', cell=ship['cell'], excel=ship.get('value'), oracle=now, state=state(ship.get('value'), now, True)))
    lines = [l for l in (order.get('order_lines') if order.get('quantity_basis') == 'order_demo' else order.get('lines')) or order.get('lines') or []
             if any(s.get('quantity') for s in l.get('sizes') or [])]
    colours = [(l.get('provenance') or {}).get('color', {}).get('cell') for l in lines]
    if any(colours):
        from_file.append(dict(label='Colours', cell=_span(colours), excel=f'{len(lines)} colours', oracle=f'{len(sheet["lines"])} colours',
                              state='same' if not (check or {}).get('missing_lines') and not (check or {}).get('extra_lines') else 'different'))
    qty = [s.get('source', {}).get('cell') for l in lines for s in l.get('sizes') or [] if s.get('quantity')]
    if any(qty) and check:
        cells = check['cells']
        from_file.append(dict(label='Size quantities', cell=_span(qty), excel=f'{cells["count"]} quantities · {check["pieces"]["excel"]} pcs',
                              oracle=f'{cells["count"] - len(cells["different"])} same · {check["pieces"]["oracle"]} pcs',
                              state='same' if not cells['different'] and check['pieces']['excel'] == check['pieces']['oracle'] else 'different'))
    kept = []
    for x in (receipt or {}).get('not_saved') or []:
        values = [str(v) for v in x.get('values') or []]
        what = ', '.join(values[:3]) + (f' +{len(values) - 3}' if len(values) > 3 else '') if values else f'{x.get("count", 0)} rows'
        p = prov.get(x.get('field')) or {}
        path = (x.get('path') or '').split('.')[-1]
        items = order.get(path)
        if path == 'lines':                             # the line's own cells, not the size names in the heading row
            items = [(l.get('provenance') or {}).get('color') for l in items or []]
        cell = x.get('cell') or p.get('cell') or (_rows_of(items) if path in ('fabrics', 'accessories', 'lines') else None)
        if not cell and p.get('derivation'):            # not in the file: say where the value came from
            what = f'{what} ({p["derivation"][0].lower()}{p["derivation"][1:]})'
        kept.append(dict(label=x['label'], cell=cell, excel=what,
                         why=('No order field for this yet' if x.get('field') == 'other' else 'Oracle has no field for this')
                         if x.get('kept') == 'poppys' else 'Oracle field not confirmed yet'))
    return dict(from_file=from_file, filled=filled, kept=kept)


MAX_GRID_ROWS = 2000
MAX_GRID_COLS = 60


def _cells_in(value, out):
    """Every Excel cell address cited inside a read item (fabric row, trim row, yarn split)."""
    if isinstance(value, dict):
        if isinstance(value.get('cell'), str) and value['cell']:
            out.add(value['cell'])
        for x in value.values():
            _cells_in(x, out)
    elif isinstance(value, list):
        for x in value:
            _cells_in(x, out)
    return out


def _ref(cell):
    m = re.fullmatch(r'([A-Z]{1,3})(\d+)', cell or '')
    if not m:
        return None
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


def _region_note(region):
    if region.get('kind') == 'zeros':
        return 'none', 'All 0 — nothing to save'
    if region.get('kind') == 'repeat':
        m = re.search(r'repeat rows? ([\d–-]+)', region.get('text') or '')
        return 'none', f'Same numbers as rows {m.group(1)} — saved once' if m else 'A copy of the order quantities — saved once'
    where = f' in “{region["section"]}”' if region.get('section') else ''
    return 'unused', f'Calculation column{where} — not part of the order, not used'


def excel_grid(order, sheet, xmap, check, coverage, oracle_sheet):
    """The Excel sheet the order was read from, cell by cell as uploaded, each cell marked with where its value is now:
    'oracle' (saved in Oracle, same value), 'diff' (Oracle holds something else now), 'kept' (not in Oracle, kept in
    POPPYS with the import), 'unused' (numbers that are not part of the order), 'none' (nothing to save: all 0, a copy)
    or '' (headings and labels). A note says it in words. {name, rows, cols, merged, hidden_rows, hidden_cols, cells:
    [[row, col, value, status, note]], truncated}."""
    marks = {}

    def mark(cell, status, note):
        if cell and cell not in marks:
            marks[cell] = (status, note)

    for r in xmap.get('from_file') or []:
        if r['cell'] and '–' not in r['cell']:
            status = {'same': 'oracle', 'changed': 'oracle', 'different': 'diff'}[r['state']]
            mark(r['cell'], status, f'{r["label"]} → in Oracle: {r["oracle"]}' + (' (changed before saving)' if r['state'] == 'changed' else ''))
    diffs = {(_key(d['colour']), _key(d['size'])): d['oracle'] for d in (check or {}).get('cells', {}).get('different', [])}
    lines = [l for l in (order.get('order_lines') if order.get('quantity_basis') == 'order_demo' else order.get('lines')) or order.get('lines') or []
             if any(s.get('quantity') for s in l.get('sizes') or [])]
    by_row = {}
    for c in sheet.get('cells') or []:
        by_row.setdefault(c['row'], []).append(c)
    olines = oracle_sheet.get('lines') or []
    for i, l in enumerate(lines):
        colour = (l.get('provenance') or {}).get('color') or {}
        now = olines[i]['colour'] if len(olines) == len(lines) else None
        mark(colour.get('cell'), 'oracle', f'Colour → in Oracle: {now}' if now else 'Colour — saved in Oracle')
        rc = _ref(colour.get('cell'))
        if rc and l.get('label'):
            for c in by_row.get(rc[0], []):
                if c.get('value') == l['label']:
                    mark(c['cell'], 'kept', 'Line label — not in Oracle (Oracle has no field for this), kept in POPPYS')
        for s in l.get('sizes') or []:
            cell = (s.get('source') or {}).get('cell')
            if not s.get('quantity'):
                continue
            k = (_key(l.get('color')), _key(s.get('size')))
            if k in diffs:
                mark(cell, 'diff', f'{l.get("color")} · {s.get("size")}: Excel {s["quantity"]} → Oracle now {diffs[k]}')
            else:
                mark(cell, 'oracle', f'{l.get("color")} · {s.get("size")} = {s["quantity"]} pcs → saved in Oracle')
    packs = order.get('packs') or {}
    pieces = (check or {}).get('pieces') or {}
    if (packs.get('source') or {}).get('pieces') and pieces.get('oracle') is not None:
        same = readback._same(packs.get('pieces'), pieces['oracle'])
        mark(packs['source']['pieces'].get('cell'), 'oracle' if same else 'diff',
             f'Total pieces {packs.get("pieces")} → Oracle total {pieces["oracle"]}' + ('' if same else ' (different)'))
        mark((packs['source'].get('packs') or {}).get('cell'), 'none',
             f'Packs: {packs.get("packs")} packs = {packs.get("pieces")} pieces — used to check the total')
    for key, block in (('sample_lines', 'Sample'), ('order_lines', 'Order')):     # the quantity block that was not saved
        for l in order.get(key) or []:
            for s in l.get('sizes') or []:
                if (s.get('source') or {}).get('cell'):
                    mark(s['source']['cell'], 'none', 'All 0 — nothing to save' if not s.get('quantity')
                         else f'{block} quantity — not saved; the other quantity block was saved')
    for r in xmap.get('kept') or []:
        if r.get('cell') and _ref(r['cell']):
            mark(r['cell'], 'kept', f'{r["label"]} — not in Oracle ({r["why"].lower()}), kept in POPPYS')
    for section, label in (('fabrics', 'Fabric'), ('accessories', 'Trims / accessories')):
        for cell in sorted(_cells_in(order.get(section) or [], set())):
            mark(cell, 'kept', f'{label} — not in Oracle yet (Oracle field not confirmed), kept in POPPYS')
    for region in (coverage or {}).get('regions') or []:
        status, note = _region_note(region)
        rows = region.get('rows') or []
        for row in range(rows[0], rows[-1] + 1) if rows else ():
            for col in region.get('columns') or []:
                mark(f'{col}{row}', status, note)
    cells = [c for c in sheet.get('cells') or [] if c['row'] <= MAX_GRID_ROWS and c['col'] <= MAX_GRID_COLS]
    shown_rows = {c['row'] for c in cells if not c.get('hidden')}
    shown_cols = {c['col'] for c in cells if not c.get('hidden')}
    hidden_cols = sorted({c['col'] for c in cells if c.get('hidden')} - shown_cols)
    hidden_rows = sorted({c['row'] for c in cells if c.get('hidden') and c['col'] not in hidden_cols} - shown_rows)
    return dict(name=sheet.get('name'), rows=min(sheet.get('rows') or 0, MAX_GRID_ROWS), cols=min(sheet.get('cols') or 0, MAX_GRID_COLS),
                merged=list(sheet.get('merged') or []), hidden_rows=hidden_rows, hidden_cols=hidden_cols,
                truncated=(sheet.get('rows') or 0) > MAX_GRID_ROWS or (sheet.get('cols') or 0) > MAX_GRID_COLS,
                cells=[[c['row'], c['col'], c.get('value'), *marks.get(c['cell'], ('', ''))] for c in cells])


def proof_sql(mapping, present, ordmasid, sheet, name=lambda t: t):
    """Read-only SELECTs a person can paste into SQL Developer (connected as the order schema) to see this order straight
    from Oracle, with the master IDs decoded to names, and what each should show (from the order read now):
    [{title, sql, expect}]. Only mapped columns this database has are used; ``name`` qualifies a table with its schema."""
    head, line, size = tables(mapping)
    spec = mapping['tables']; n = int(ordmasid)

    def select(table, alias):
        cols = [c for c in spec[table]['columns'] if c in present(table)]
        masters = {c: m for c, m in master_of(mapping, table).items() if c in cols and present(m[0])}
        out = [f'{alias}.{c}' + (f', m_{c.lower()}.{masters[c][2]} AS {c[:25]}_NAME' if c in masters else '') for c in cols]
        joins = [f'LEFT JOIN {name(mt)} m_{c.lower()} ON m_{c.lower()}.{key} = {alias}.{c}' for c, (mt, key, _) in masters.items()]
        return out, joins

    def where(table, alias):
        c = spec[table]['key'] if table == head else column(mapping, table, parent=head)
        return f'WHERE {alias}.{c} = {n}'

    out = []
    cols, joins = select(head, 'o')
    out.append(dict(title='1. The order (header)', expect='1 row',
                    sql='\n'.join([f'SELECT {", ".join(cols)}', f'FROM {name(head)} o', *joins, where(head, 'o') + ';'])))
    row = lambda t: next((c for c, x in spec[t]['columns'].items() if x.get('field') == 'calc.row' and c in present(t)), None)
    if line and column(mapping, line, parent=head):
        cols, joins = select(line, 'l')
        out.append(dict(title='2. Colour lines', expect=f'{len(sheet.get("lines") or [])} rows',
                        sql='\n'.join([f'SELECT {", ".join(cols)}', f'FROM {name(line)} l', *joins, where(line, 'l'),
                                       *([f'ORDER BY l.{row(line)}'] if row(line) else [])]) + ';'))
    link = next((x['on'] for x in mapping.get('links') or [] if x.get('from') == line and x.get('to') == size and '=' in x['on']), None)
    if size and link and column(mapping, size, parent=head):
        lc, sc = (x.strip() for x in link.split('=', 1))
        colour = column(mapping, line, field='line.color'); sname = column(mapping, size, field='size.size')
        qty = column(mapping, size, field='calc.pieces'); srow = row(size) or spec[size]['key']
        ms, ml = master_of(mapping, size).get(sname), master_of(mapping, line).get(colour)
        if colour and sname and qty and lc in present(line) and sc in present(size):
            cname = f'c.{ml[2]}' if ml else f'l.{colour}'; zname = f'z.{ms[2]}' if ms else f's.{sname}'
            pieces = sheet.get('total') or 0
            out.append(dict(title='3. Colour × size, one row per colour (like the Excel)',
                            expect=f'{len(sheet.get("lines") or [])} rows · {pieces:,} pieces in all',
                            sql='\n'.join([
                                f"SELECT {cname} AS COLOUR,",
                                f"       LISTAGG({zname} || ' = ' || s.{qty}, '  |  ') WITHIN GROUP (ORDER BY s.{srow}) AS SIZE_QUANTITIES,",
                                f'       SUM(s.{qty}) AS PIECES',
                                f'FROM {name(size)} s',
                                f'JOIN {name(line)} l ON l.{column(mapping, line, parent=head)} = s.{column(mapping, size, parent=head)} AND l.{lc} = s.{sc}',
                                *([f'LEFT JOIN {name(ml[0])} c ON c.{ml[1]} = l.{colour}'] if ml else []),
                                *([f'LEFT JOIN {name(ms[0])} z ON z.{ms[1]} = s.{sname}'] if ms else []),
                                where(size, 's'), f'GROUP BY l.{row(line) or lc}, {cname}', f'ORDER BY l.{row(line) or lc};'])))
            total = column(mapping, head, field='calc.pieces')
            out.append(dict(title='4. Totals check', expect=f'{len(sheet.get("lines") or [])} lines · {pieces:,} pieces, header total the same',
                            sql='\n'.join([
                                'SELECT ' + (f'o.{total} AS HEADER_TOTAL,\n       ' if total and total in present(head) else '') +
                                f'(SELECT COUNT(*) FROM {name(line)} l {where(line, "l")}) AS COLOUR_LINES,',
                                f'       (SELECT COUNT(*) FROM {name(size)} s {where(size, "s")}) AS SIZE_ROWS,',
                                f'       (SELECT SUM(s.{qty}) FROM {name(size)} s {where(size, "s")}) AS SIZE_TOTAL',
                                f'FROM {name(head)} o {where(head, "o")};'])))
    return out
