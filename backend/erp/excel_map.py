"""The person's own Excel → Oracle mapping (New order → "Map to Oracle").

A target is one Oracle column, written ``TABLE.COLUMN``. The person selects cells of the uploaded sheet and gives them
to a target; ``build`` turns those cells into the draft order the planner already plans and the writer already saves.
Nothing is added that the file does not hold: a target with no cells stays blank (NULL in Oracle), unless the person
typed a value for it on the preview.

What a target does with its cells depends on the kind of column (``kinds``):

* ``field``  — an order field POPPYS knows (Buyer, Style, PO …): the cells' text, joined.
* ``extra``  — any other column of the order (ORDMAS) or of a colour line (ORDSTYLE): one value, or one value per line.
* ``line``   — ORDSTYLE colour: each selected cell is one colour line (its Excel row ties sizes and quantities to it).
* ``size``   — ORDSIZE size: each selected cell is a size name (its Excel column ties quantities to it).
* ``qty``    — ORDSIZE quantity: each cell is the quantity of the line in its row and the size in its column.
* ``row``    — a column of a row table of the order (fabric, yarn, trims): one Oracle row per Excel row selected.
* ``auto``   — keys, links, totals and fixed values POPPYS sets itself; they cannot be mapped.
"""
from __future__ import annotations
import re
from copy import deepcopy

from .. import workbooks as W

ORDER, LINES, SIZES = 'ORDMAS', 'ORDSTYLE', 'ORDSIZE'
ROW_TABLES = ('ORDFAB', 'ORDYARN', 'ORDACC')
TABLES = (ORDER, LINES, SIZES) + ROW_TABLES
MAX_CELLS = 20000
TARGET = re.compile(r'^([A-Z][A-Z0-9_$#]{0,29})\.([A-Z][A-Z0-9_$#]{0,29})$')
# order fields the person sees as one value, written on the lines (SHIPDT holds the order's ship date)
LINE_COPIES_OF_ORDER = ('order.ship_date',)


def kinds(mapping, table, columns):
    """{column: (kind, field or note)} for every column of a target table. columns: {name: kb column}."""
    spec = mapping['tables'].get(table) or {}
    out = {}
    for name in columns:
        c = (spec.get('columns') or {}).get(name) or {}
        verified = spec.get('status') == 'verified' and c.get('status') == 'verified'
        field = c.get('field') or ''
        if c.get('key') or name == spec.get('key'):
            out[name] = ('auto', 'Record ID, given by Oracle when the order is saved')
        elif 'parent' in c or (table != ORDER and name == 'ORDMASID'):
            out[name] = ('auto', 'Link to the order, set when it is saved')
        elif table in ROW_TABLES and name == f'{table}ROW':
            out[name] = ('auto', 'Row number, 1, 2, 3 … in the order of the Excel rows')
        elif table == SIZES and name == _qty_column(mapping):
            out[name] = ('qty', 'quantity')
        elif verified and 'copy' in c:
            out[name] = ('auto', f'Same as {c["copy"]}')
        elif verified and 'value' in c:
            out[name] = ('auto', f'Always “{c["value"]}” for a sample order')
        elif verified and field.startswith('calc.'):
            out[name] = ('auto', {'calc.pieces': 'Total pieces, added up from the quantities', 'calc.subno': 'Line link number (order:line)',
                                  'calc.row': 'Row number, 1, 2, 3 …'}.get(field, 'Worked out by POPPYS'))
        elif verified and table == ORDER and field.startswith('order.'):
            out[name] = ('field', field[6:])
        elif verified and table == LINES and field in LINE_COPIES_OF_ORDER:
            out[name] = ('field', field[6:])
        elif verified and table == LINES and field == 'line.color':
            out[name] = ('line', 'color')
        elif verified and table == SIZES and field == 'size.size':
            out[name] = ('size', 'size')
        elif table == SIZES:
            out[name] = ('auto', 'Not used for size rows')
        elif table in ROW_TABLES:
            out[name] = ('row', None)
        else:
            out[name] = ('extra', None)
    return out


def _qty_column(mapping):
    spec = mapping['tables'].get(SIZES) or {}
    return next((n for n, c in spec.get('columns', {}).items() if c.get('field') == 'calc.pieces' and n.startswith('R')),
                next((n for n, c in spec.get('columns', {}).items() if c.get('field') == 'calc.pieces'), None))


def catalog(mapping, kb):
    """The Oracle side of the mapping screen: each target table with its columns, their type and what mapping them does."""
    out = []
    for table in TABLES:
        t = kb.table(table)
        if not t:
            continue
        cols = t['columns']; k = kinds(mapping, table, cols)
        spec = mapping['tables'].get(table) or {}
        out.append(dict(table=table, business=spec.get('business') or t['business'], level=spec.get('level'),
                        rows={ORDER: 'one row for the order', LINES: 'one row per colour', SIZES: 'one row per colour and size'}
                        .get(table, 'one row per Excel row you map'),
                        columns=[dict(name=n, label=(spec.get('columns', {}).get(n) or {}).get('label') or c['business'],
                                      type=c['type'], length=c.get('length'), precision=c.get('precision'), scale=c.get('scale'),
                                      kind=k[n][0], field=k[n][1] if k[n][0] in ('field', 'line', 'size', 'qty') else None,
                                      note=k[n][1] if k[n][0] == 'auto' else None,
                                      system=c['role'] == 'system', standard=n in (spec.get('columns') or {}))
                                 for n, c in cols.items()]))
    return out


# ---------------------------------------------------------------- the current order as targets
def _cell(src, sheet):
    if isinstance(src, dict) and src.get('cell') and (not src.get('sheet') or src['sheet'] == sheet):
        return str(src['cell']).upper()
    return None


def targets_of(order, mapping, sheet):
    """{'TABLE.COLUMN': [cells]} that the order read automatically came from: the starting point of the screen."""
    out = {}
    head = {f: col for col, (kind, f) in kinds(mapping, ORDER, mapping['tables'][ORDER]['columns']).items() if kind == 'field'}
    for f, col in head.items():
        c = _cell((order.get('provenance') or {}).get(f), sheet)
        if c:
            out[f'{ORDER}.{col}'] = [c]
    line_cols = kinds(mapping, LINES, mapping['tables'][LINES]['columns'])
    for col, (kind, f) in line_cols.items():
        if kind == 'field':
            c = _cell((order.get('provenance') or {}).get(f), sheet)
            if c:
                out[f'{LINES}.{col}'] = [c]
    size_cols = kinds(mapping, SIZES, mapping['tables'][SIZES]['columns'])
    color = next((c for c, (k, _) in line_cols.items() if k == 'line'), None)
    size = next((c for c, (k, _) in size_cols.items() if k == 'size'), None)
    qty = _qty_column(mapping)
    for line in order.get('lines') or []:
        c = _cell((line.get('provenance') or {}).get('color'), sheet)
        if c and color:
            out.setdefault(f'{LINES}.{color}', []).append(c)
        for s in line.get('sizes') or []:
            sc = _cell(s.get('size_source'), sheet); qc = _cell(s.get('source'), sheet)
            if sc and size and sc not in out.get(f'{SIZES}.{size}', []):
                out.setdefault(f'{SIZES}.{size}', []).append(sc)
            if qc and qty:
                out.setdefault(f'{SIZES}.{qty}', []).append(qc)
        for col, x in (line.get('oracle_columns') or {}).items():
            c = _cell(x, sheet)
            if c:
                out.setdefault(f'{LINES}.{col}', []).append(c)
    for col, x in ((order.get('oracle_columns') or {}).get(ORDER) or {}).items():
        out[f'{ORDER}.{col}'] = list(x.get('cells') or [])
    for table, records in (order.get('oracle_rows') or {}).items():
        for rec in records:
            for col, x in rec.items():
                out.setdefault(f'{table}.{col}', []).extend(x.get('cells') or [])
    return out


# ---------------------------------------------------------------- cells → order
def _refs(cells):
    out = []
    for ref in cells or []:
        r, c = W.cell_ref(ref)
        out.append((r, c))
    return out


def _shown(v):
    """A cell value as text for a text column: whole numbers without “.0”."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return W.text(v)


def _typed(value, meta):
    """A value of the file for an Oracle column of this type: a date as YYYY-MM-DD, a number as a number, else text.
    A value that does not fit stays as it is, so the preview shows the problem on it (it is never changed silently)."""
    if value in (None, ''):
        return None
    t = (meta or {}).get('type') or 'VARCHAR2'
    if t == 'DATE' or t.startswith('TIMESTAMP'):
        d, _ = W.iso_date(value)
        return d or W.text(value)
    if t == 'NUMBER':
        n = W.number(value)
        return (int(n) if isinstance(n, float) and n.is_integer() else n) if n is not None else W.text(value)
    return _shown(value)


def validate(targets, table_meta):
    """Targets as {target: [cells]}: known tables and columns, mappable kinds and real cell addresses only."""
    clean = {}; total = 0
    for target, cells in (targets or {}).items():
        m = TARGET.match(str(target))
        if not m or m.group(1) not in table_meta or m.group(2) not in table_meta[m.group(1)]['kinds']:
            raise ValueError(f'{target} is not a column POPPYS can write.')
        if table_meta[m.group(1)]['kinds'][m.group(2)][0] == 'auto':
            raise ValueError(f'{target} is set by POPPYS itself; it cannot be taken from the Excel file.')
        refs = list(dict.fromkeys(str(c).strip().upper() for c in cells or [] if str(c).strip()))
        for ref in refs:
            W.cell_ref(ref)
        total += len(refs)
        if refs:
            clean[target] = refs
    if total > MAX_CELLS:
        raise ValueError(f'At most {MAX_CELLS:,} cells can be mapped at once.')
    return clean


KEEP = ('sheet', 'fabrics', 'accessories', 'other_fields', 'qty_unit', 'pieces_per_unit', 'season', 'exporter', 'against',
        'department', 'gender', 'packing_type', 'components', 'merch', 'sample_type', 'quantity_basis')


def build(sheet, targets, typed, base, table_meta):
    """The order for these targets. base: the order read before (fields Oracle has no column for, the quantity unit and
    the fabric / trims rows read from the file are kept from it). typed: {target: value} the person typed."""
    cells = {(c['row'], c['col']): c for c in sheet['cells']}
    value = lambda rc: (cells.get(rc) or {}).get('value')                                        # noqa: E731
    src = lambda rc: W.source(sheet['name'], W.address(rc[1], rc[0]), value(rc), (cells.get(rc) or {}).get('formula'))  # noqa: E731
    o = W.base_order()
    for k in KEEP:
        if k in base:
            o[k] = deepcopy(base[k])
    o['provenance'] = {k: v for k, v in (base.get('provenance') or {}).items() if k in ('qty_unit', 'pieces_per_unit', 'quantity_basis')}
    o['sheet'] = sheet['name']; o['mapped_by_person'] = True
    o['quantity_basis'] = base.get('quantity_basis') if base.get('quantity_basis') in ('sample', 'order_demo') else 'order_demo'
    # fields Oracle has a verified column for are read only from the targets (blank when not mapped)
    fields = {f for t in table_meta.values() for kind, f in t['kinds'].values() if kind == 'field'}
    for f in fields:
        o[f] = ''
    header = {}; per_line = {}; rows = {}
    color = size = qty = None
    for target, refs in targets.items():
        table, col = target.split('.')
        kind, field = table_meta[table]['kinds'][col]
        rc = sorted(_refs(refs))
        if kind == 'field':
            text = ' '.join(t for t in (_shown(value(x)) for x in rc) if t)
            raw = value(rc[0]) if len(rc) == 1 else text
            o[field] = raw if raw not in (None, '') else ''
            o['provenance'][field] = dict(src(rc[0]), **({'cells': refs} if len(rc) > 1 else {}))
        elif kind == 'line':
            color = (col, rc)
        elif kind == 'size':
            size = (col, rc)
        elif kind == 'qty':
            qty = (col, rc)
        elif kind == 'extra' and table == ORDER:
            text = ' '.join(t for t in (_shown(value(x)) for x in rc) if t)
            raw = value(rc[0]) if len(rc) == 1 else text
            header[col] = dict(value=_typed(raw, table_meta[table]['meta'].get(col)), sheet=sheet['name'], cell=refs[0], cells=refs)
        elif kind == 'extra':
            per_line[col] = rc
        elif kind == 'row':
            for r, c in rc:
                rows.setdefault(table, {}).setdefault(r, {}).setdefault(col, []).append((r, c))
    for target, text in (typed or {}).items():
        table, col = target.split('.')
        kind, field = table_meta[table]['kinds'][col]
        text = W.text(text)
        if not text:
            continue
        if kind == 'field':
            o[field] = text; o['provenance'][field] = dict(derivation='Typed on the preview', reason='person', value=text)
        elif kind == 'extra' and table == ORDER:
            header[col] = dict(value=_typed(text, table_meta[table]['meta'].get(col)), typed=True)
    if header:
        o['oracle_columns'] = {ORDER: header}

    # colour lines by Excel row; sizes by Excel column; each quantity where its row and column meet
    lines = {}
    for r, c in (color[1] if color else []):
        lines[r] = dict(label='', color=_shown(value((r, c))), provenance={'color': src((r, c))}, sizes=[], declared_total=None, row=r)
    names = {c: (_shown(value((r, c))), src((r, c))) for r, c in (size[1] if size else [])}
    for r, c in (qty[1] if qty else []):
        v = value((r, c))
        if v in (None, '') or (isinstance(v, str) and not v.strip()):
            continue                                   # blank in the file: no size row (not a 0)
        line = lines.setdefault(r, dict(label='', color='', provenance={}, sizes=[], declared_total=None, row=r))
        n = W.number(v)
        name, name_src = names.get(c, ('', None))
        line['sizes'].append(dict(size=name, quantity=(int(n) if n is not None and float(n).is_integer() else n) if n is not None else v,
                                  source=src((r, c)), size_source=name_src))
    for col, rc in per_line.items():
        for r, c in rc:
            if r in lines and value((r, c)) not in (None, ''):
                lines[r].setdefault('oracle_columns', {})[col] = dict(
                    value=_typed(value((r, c)), table_meta[LINES]['meta'].get(col)), sheet=sheet['name'], cell=W.address(c, r))
            elif value((r, c)) not in (None, ''):
                o['warnings'].append(f'{W.address(c, r)} ({LINES}.{col}) is not on the row of a colour line, so it was not used.')
    o['lines'] = [lines[r] for r in sorted(lines)]
    for line in o['lines']:
        line.pop('row', None)

    # row tables: one Oracle row per Excel row; cells of several Excel columns for one Oracle column are joined
    out_rows = {}
    for table, by_row in rows.items():
        recs = []
        for r in sorted(by_row):
            rec = {}
            for col, rc in by_row[r].items():
                rc = sorted(rc, key=lambda x: x[1])
                vals = [value(x) for x in rc]
                raw = vals[0] if len(vals) == 1 else ' '.join(t for t in (_shown(v) for v in vals) if t)
                v = _typed(raw, table_meta[table]['meta'].get(col))
                if v not in (None, ''):
                    rec[col] = dict(value=v, sheet=sheet['name'], cell=W.address(rc[0][1], r), cells=[W.address(c, rr) for rr, c in rc])
            if rec:
                recs.append(rec)
        if recs:
            out_rows[table] = recs
    if out_rows:
        o['oracle_rows'] = out_rows
    return W.finish(o)
