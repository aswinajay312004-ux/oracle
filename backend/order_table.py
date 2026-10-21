"""Single-table mode: one Excel order -> rows of ONE Oracle table (GARMENT_DEMO.EXCEL_ORDERS by default).

Like the client's own flat ORDERENTRY table, the table has one row per size; the order details repeat on every row.
Each column's Oracle comment holds the Excel labels it accepts ("FOB date|Ship date|Delivery date"), so matching an
Excel label to a column is read from Oracle itself: a column added later is matched the same way.

Value priority per column: the reviewer's mapping > the structural reader's field > an Excel label match.
Nothing is invented: a column without a value stays NULL."""
from __future__ import annotations
from datetime import datetime
import json
import re
import oracledb
from .config import OWNER, setting
from decimal import Decimal
from .oracle_store import IDENT, bind_value, blank, connect, convert, digest, normalize, plain, read_result, same
from .workbooks import norm, source

TABLE = setting('ORDER_TABLE', 'EXCEL_ORDERS').upper()

# name, Oracle type, required, level, parsed order field, Excel labels (first = business label)
SPEC = [
    ('ENTRY_ID', 'NUMBER(18)', True, 'system', None, ['Row ID']),
    ('ORDER_KEY', 'NUMBER(18)', True, 'system', None, ['Order ID']),
    ('IMPORT_ID', 'VARCHAR2(64 CHAR)', True, 'system', None, ['Import ID']),
    ('IONO', 'VARCHAR2(30 CHAR)', False, 'order', 'order_ref', ['OC No', 'OCN', 'OC number', 'Order no', 'Order number', 'Order reference', 'IO No']),
    ('BUYERNAME', 'VARCHAR2(100 CHAR)', False, 'order', 'buyer', ['Buyer', 'Buyer name', 'Customer', 'Customer name']),
    ('SEASON', 'VARCHAR2(30 CHAR)', False, 'order', 'season', ['Season']),
    ('STYLENO', 'VARCHAR2(60 CHAR)', False, 'order', 'style', ['Style no', 'Item', 'Style', 'Style number', 'Item number']),
    ('STYLEDESC', 'VARCHAR2(200 CHAR)', False, 'order', 'description', ['Description', 'Garment description', 'Style description']),
    ('PONUM', 'VARCHAR2(60 CHAR)', False, 'order', 'po_number', ['PO number', 'Contract', 'PO', 'PO no', 'Contract no']),
    ('MERCH', 'VARCHAR2(60 CHAR)', False, 'order', 'merch', ['Merchandiser', 'Merch']),
    ('DESCOUNTRY', 'VARCHAR2(100 CHAR)', False, 'order', None, ['Destination', 'Destination country', 'Country']),
    ('ORDERDATE', 'DATE', False, 'order', 'order_date', ['Order date', 'OC date', 'Confirmation date', 'PO date']),
    ('BOMDATE', 'DATE', False, 'order', None, ['BOM date']),
    ('SHIPDT', 'DATE', False, 'order', 'ship_date', ['FOB date', 'Ship date', 'Delivery date', 'Ex-factory date']),
    ('FABIHDATE', 'DATE', False, 'order', None, ['Fab IH date', 'Fabric in-house date']),
    ('PACKQTY', 'NUMBER(10)', False, 'order', None, ['Qty in packs', 'Packs']),
    ('OQTY', 'NUMBER(10)', False, 'order', None, ['Qty in pieces', 'Total pieces', 'Total quantity']),
    ('PRICE', 'NUMBER(12,4)', False, 'order', None, ['Price in USD', 'Price', 'Rate', 'FOB price']),
    ('REMARKS', 'VARCHAR2(500 CHAR)', False, 'order', None, ['Remarks', 'Notes']),
    ('ORDSTYLEROW', 'NUMBER(5)', False, 'line', None, ['Line no']),
    ('GARMENT', 'VARCHAR2(100 CHAR)', False, 'line', None, ['Garment']),
    ('COMBO', 'VARCHAR2(250 CHAR)', False, 'line', None, ['Colour', 'Color', 'Combo']),
    ('ORDSIZEROW', 'NUMBER(5)', False, 'size', None, ['Size row']),
    ('SIZES', 'VARCHAR2(30 CHAR)', False, 'size', None, ['Size']),
    ('ORDERQTY', 'NUMBER(10)', False, 'size', None, ['Quantity']),
    ('QTYTYPE', 'VARCHAR2(10 CHAR)', False, 'size', None, ['Quantity type']),
    ('SOURCE_FILE', 'VARCHAR2(200 CHAR)', False, 'system', None, ['Excel file']),
    ('SOURCE_CELL', 'VARCHAR2(60 CHAR)', False, 'system', None, ['Excel cell']),
    ('CREATEDBY', 'VARCHAR2(50 CHAR)', False, 'system', None, ['Approved by']),
    ('CREATEDON', 'DATE', False, 'system', None, ['Saved on']),
]
SYSTEM = {c[0] for c in SPEC if c[3] == 'system'}
LEVEL = {c[0]: c[3] for c in SPEC}
FIELD = {c[0]: c[4] for c in SPEC if c[4]}
PURPOSE = 'Orders read from Excel. One row per size; the order details repeat on every row.'


def ddl():
    """Statements for scripts/create_order_table.py (run once, additive)."""
    cols = ',\n '.join(f'"{n}" {t}{" DEFAULT SYSDATE" if n == "CREATEDON" else ""}{" NOT NULL" if req else ""}' for n, t, req, *_ in SPEC)
    pk = f'PK_{TABLE}'[:30]
    out = [f'create table {OWNER}.{TABLE} (\n {cols},\n constraint "{pk}" primary key ("ENTRY_ID"))',
           f'create index {OWNER}.{TABLE}_ORDER on {OWNER}.{TABLE}("ORDER_KEY")',
           f"comment on table {OWNER}.{TABLE} is '{PURPOSE}'"]
    out += [f"comment on column {OWNER}.{TABLE}.\"{n}\" is '{'|'.join(labels)}'" for n, _, _, _, _, labels in SPEC]
    return out


def columns(conn):
    """Live columns of the order table with their Excel labels (from Oracle column comments)."""
    c = conn.cursor()
    c.execute('''select t.column_name, t.data_type, t.data_length, t.data_precision, t.data_scale, t.nullable, t.data_default, m.comments
        from all_tab_columns t left join all_col_comments m on m.owner = t.owner and m.table_name = t.table_name and m.column_name = t.column_name
        where t.owner = :o and t.table_name = :t order by t.column_id''', o=OWNER, t=TABLE)
    out = {}
    for name, kind, length, precision, scale, nullable, default, comment in c:
        labels = [x.strip() for x in (comment or '').split('|') if x.strip()] or [name.replace('_', ' ').title()]
        out[name] = dict(type=kind, length=length, precision=precision, scale=scale, nullable=nullable,
                         default=None if default is None else str(default).strip(), label=labels[0],
                         synonyms={norm(x) for x in labels} | {norm(name.replace('_', ' '))}, level=LEVEL.get(name, 'order'))
    return out


def exists(conn):
    c = conn.cursor()
    c.execute('select count(*) from all_tables where owner = :o and table_name = :t', o=OWNER, t=TABLE)
    return c.fetchone()[0] > 0


# ---------------------------------------------------------------- automatic label matching

def header_limit(sheet):
    """Labels are read from the header area, above the first size block, so a later table heading
    (for example 'Order Qty' in a fabric schedule) is never taken for an order field."""
    rows = [c['row'] for c in sheet['cells'] if norm(c['value']) in ('size', 'sizes', 'garment')]
    return min(rows) if rows else 60


def label_values(sheets, cols, empty=None):
    """{COLUMN: {value, source}} for order-level columns whose Excel label appears in the workbook header.
    The value is the cell right of the label; when that cell is itself a known label (a header row like
    'OCN | QTY(IN PACKS) | QTY(IN PIECES)') the value under the label is used. Nothing is guessed beyond that."""
    wanted = {name: meta['synonyms'] for name, meta in cols.items() if meta['level'] == 'order'}
    known = set().union(*wanted.values()) if wanted else set()
    out = {}
    for sheet in (s for s in sheets if not s['hidden']):
        cells = {(c['row'], c['col']): c for c in sheet['cells']}
        limit = header_limit(sheet)
        for c in sorted(sheet['cells'], key=lambda x: (x['row'], x['col'])):
            if c['row'] >= limit or not isinstance(c['value'], str):
                continue
            text = c['value']; label, _, inline = text.partition(':')
            for name, syns in wanted.items():
                if name in out:
                    continue
                if inline.strip() and norm(label) in syns:
                    out[name] = dict(value=inline.strip(), source=dict(source(sheet['name'], c['cell'], text, c['formula']), via=f'Excel label "{label.strip()}"'))
                    continue
                if norm(text) not in syns:
                    continue
                right = next((cells[(c['row'], x)] for x in range(c['col'] + 1, c['col'] + 4) if (c['row'], x) in cells), None)
                cell = right if right is not None and not (isinstance(right['value'], str) and norm(right['value']) in known) else cells.get((c['row'] + 1, c['col']))
                if cell is None or blank(cell['value']):
                    if empty is not None:
                        empty.add(name)  # the label is in Excel but its value cell is empty
                    continue
                out[name] = dict(value=cell['value'], source=dict(source(sheet['name'], cell['cell'], cell['value'], cell['formula']), via=f'Excel label "{text.strip()}"'))
    return out


# ---------------------------------------------------------------- plan

def build_plan(cols, orders, sheets, filename='', existing_refs=frozenset(), committed_file=None):
    """Exact rows and column values one approval writes into the order table."""
    errors, warnings, info = [], [], []
    ops = []; labelled_blank = set(); labels = label_values(sheets, cols, labelled_blank)
    totals = dict(orders=len(orders), lines=0, pieces=0)
    if not orders or len(orders) > 50:
        errors.append('Import must contain 1–50 orders.')
    seen = set()
    for oi, o in enumerate(orders):
        prefix = f'Order {oi + 1}' if len(orders) > 1 else 'Order'; prov = o.get('provenance', {})
        head, head_info = {}, {}
        for name, meta in cols.items():
            if meta['level'] != 'order':
                continue
            chosen = None
            bound = (o.get('table_values') or {}).get(name)
            if bound is not None:
                chosen = (bound.get('value'), bound.get('source') or {}, 'manual' if not (bound.get('source') or {}).get('cell') else 'excel')
            elif FIELD.get(name) and not blank(o.get(FIELD[name])):
                src = prov.get(FIELD[name]) or {}
                chosen = (o[FIELD[name]], src, 'excel' if src.get('cell') else 'manual')
            elif name in labels:
                chosen = (labels[name]['value'], labels[name]['source'], 'excel')
            if chosen is None or blank(chosen[0]):
                continue
            try:
                head[name] = convert(meta, chosen[0])
            except ValueError as exc:
                errors.append(f'{prefix}: {meta["label"]} ({name}) {exc} (Excel value "{chosen[0]}").'); continue
            head_info[name] = dict(origin=chosen[2], source=chosen[1], raw=chosen[0], label=meta['label'])
        ref = str(head.get('IONO') or '').strip()
        if not ref:
            warnings.append(f'{prefix}: there is no OC number, so a second upload of the same order cannot be recognised. Add it if you have it.')
        elif normalize(ref) in seen:
            errors.append(f'{prefix}: OC number {ref} appears twice in this workbook.')
        elif normalize(ref) in existing_refs:
            errors.append(f'{prefix}: OC number {ref} is already saved. Open Orders to see or edit it, or use a new OC number.')
        seen.add(normalize(ref))
        if not ref and committed_file:
            errors.append(f'{prefix}: this exact workbook was already saved (import {committed_file[:8]}) and has no OC number to tell the orders apart.')
        basis = 'SAMPLE' if o.get('quantity_basis') == 'sample' else 'ORDER'
        factor = o.get('pieces_per_unit') or 1
        try:
            factor = int(factor); assert factor >= 1
        except (ValueError, TypeError, AssertionError):
            errors.append(f'{prefix}: pieces per unit must be a whole number of at least 1.'); factor = 1
        order_key = f'k{oi + 1}'; rows = 0; pieces = 0
        def add(values, infos, label):
            nonlocal rows
            rows += 1; key = f'{order_key}r{rows}'
            values = dict(head, **values, ENTRY_ID='$' + key, ORDER_KEY='$' + order_key, IMPORT_ID='$job', SOURCE_FILE=filename[:200] or None, CREATEDBY='$approver')
            values = {k: v for k, v in values.items() if v is not None and k in cols}
            ops.append(dict(key=key, order=oi, table=TABLE, action='insert', values=values, label=label,
                            columns=dict(head_info, **infos, ENTRY_ID=dict(origin='generated'), ORDER_KEY=dict(origin='generated', note='Same for every row of this order'),
                                         IMPORT_ID=dict(origin='generated'), CREATEDBY=dict(origin='derived', note='The admin who approves'),
                                         SOURCE_FILE=dict(origin='derived', note='Uploaded file name'))))
        for li, line in enumerate(o.get('lines', [])):
            where = f'{prefix}, line {li + 1}'; total = 0; line_rows = 0
            for si, s in enumerate(line.get('sizes', [])):
                name = str(s.get('size', '')).strip()
                if blank(s.get('quantity')):
                    if name: info.append(f'{where}: size {name} has no quantity in Excel, so no row is created for it.')
                    continue
                if not name:
                    errors.append(f'{where}: quantity {s.get("quantity")} (cell {(s.get("source") or {}).get("cell", "?")}) has no size name.'); continue
                try:
                    qty = convert(dict(type='NUMBER', precision=10, scale=0), s['quantity'])
                    assert qty >= 0
                except (ValueError, AssertionError):
                    errors.append(f'{where}, size {name}: "{s["quantity"]}" is not a whole number of pieces.'); continue
                total += qty; line_rows += 1
                src = s.get('source') or {}
                add(dict(ORDSTYLEROW=li + 1, GARMENT=str(line.get('label') or '').strip() or None, COMBO=str(line.get('color') or '').strip() or None,
                         ORDSIZEROW=si + 1, SIZES=name, ORDERQTY=qty * factor, QTYTYPE=basis,
                         SOURCE_CELL=f'{src["sheet"]}!{src["cell"]}'[:60] if src.get('cell') else None),
                    dict(ORDSTYLEROW=dict(origin='derived'), GARMENT=dict(origin='excel', source=(line.get('provenance') or {}).get('label')),
                         COMBO=dict(origin='excel', source=(line.get('provenance') or {}).get('color')), ORDSIZEROW=dict(origin='derived'),
                         SIZES=dict(origin='excel', source=s.get('size_source')), ORDERQTY=dict(origin='excel', source=src, raw=s.get('quantity')),
                         QTYTYPE=dict(origin='derived', note='Order or sample quantities, as you chose'), SOURCE_CELL=dict(origin='derived')),
                    f'{line.get("color") or "Line " + str(li + 1)} · {name}')
            if line.get('declared_total') is not None and line_rows:
                try:
                    declared = int(convert(dict(type='NUMBER', precision=12, scale=0), line['declared_total']))
                    if declared != total:
                        errors.append(f'{where}: sizes add up to {total} but the Excel total says {declared}. Check the size quantities.')
                except ValueError:
                    pass
            if line_rows:
                totals['lines'] += 1; pieces += total * factor
        if not rows:
            add({}, {}, 'Order details only')
            info.append(f'{prefix}: no size quantities in Excel; one row with the order details is saved.')
        totals['pieces'] += pieces
        if 'OQTY' in head and pieces and head['OQTY'] != pieces:
            warnings.append(f'{prefix}: the size quantities add up to {pieces}, but the Excel total pieces (OQTY) is {head["OQTY"]}.')
    for op in ops:
        for col, meta in cols.items():
            if meta['nullable'] == 'N' and col not in op['values'] and not meta.get('default'):
                errors.append(f'{TABLE}.{col}: Oracle requires a value here. Map an Excel cell to it.')
    empty = [n for n, m in cols.items() if m['level'] == 'order' and n not in set().union(*(op['values'] for op in ops) or [set()])]
    if empty:
        info.append(f'Not in this Excel, left blank: {", ".join(cols[n]["label"] for n in empty)}.')
    plan = dict(mode='table', table=TABLE, operations=ops, errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)),
                info=list(dict.fromkeys(info)), totals=totals, labelled_blank=sorted(labelled_blank - set(labels)),
                tables={TABLE: dict(label='Excel orders', purpose=PURPOSE, columns=[dict(name=n, type=m['type'], nullable=m['nullable'],
                        required=m['nullable'] == 'N', label=m['label'], level=m['level']) for n, m in cols.items()])},
                schema_fingerprint=digest({n: {k: v for k, v in m.items() if k != 'synonyms'} for n, m in cols.items()}))
    plan['hash'] = digest(plan)
    return plan


def plan(conn, orders, filehash, sheets, filename=''):
    cols = columns(conn); c = conn.cursor()
    c.execute('select JOBID from PP_IMPORT_LOG where FILEHASH=:h fetch first 1 rows only', h=filehash)
    row = c.fetchone(); existing = set()
    probe = build_plan(cols, orders, sheets, filename)
    for op in probe['operations']:
        ref = normalize(op['values'].get('IONO'))
        if ref:
            c.execute(f'SELECT 1 FROM {OWNER}.{TABLE} WHERE upper(trim(IONO))=:r FETCH FIRST 1 ROWS ONLY', r=ref)
            found = c.fetchone()
            # Every OC number POPPYS has ever saved (in any table) is reserved in PP_ORDER_KEYS.
            c.execute('SELECT 1 FROM PP_ORDER_KEYS WHERE ORDER_REF=:r', r=ref[:30])
            if found or c.fetchone():
                existing.add(ref)
    return build_plan(cols, orders, sheets, filename, existing, row[0] if row else None)


def commit(draft, expected_hash, approver):
    """One transaction for all rows, read back inside it and again on a fresh connection."""
    with connect() as conn:
        c = conn.cursor(); c.execute('SELECT ID FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10')
        previous = read_result(c, draft['id'])
        if previous:
            return previous
        checked = plan(conn, draft['orders'], draft['filehash'], draft['sheets'], draft.get('filename', ''))
        if checked['errors']:
            raise ValueError('; '.join(checked['errors']))
        if checked['hash'] != expected_hash:
            raise ValueError('The table or the preview changed. Open the preview again and review it before saving.')
        cols = columns(conn); keys = {}; inserted = []; outorders = []
        try:
            for ref in {normalize(op['values'].get('IONO')) for op in checked['operations']} - {''}:
                c.execute('INSERT INTO PP_ORDER_KEYS(ORDER_REF,JOBID) VALUES (:ref,:job)', ref=ref[:30], job=draft['id'])
            for op in checked['operations']:
                vals = {}
                for col, v in op['values'].items():
                    if isinstance(v, str) and v.startswith('$'):
                        tag = v[1:]
                        if tag == 'job': v = draft['id']
                        elif tag == 'approver': v = approver
                        else:
                            if tag not in keys:
                                c.execute('SELECT PP_RECORD_SEQ.nextval FROM dual'); keys[tag] = c.fetchone()[0]
                            v = keys[tag]
                    vals[col] = bind_value(cols[col], v)
                names = list(vals); binds = {f'v{i}': vals[n] for i, n in enumerate(names)}
                quoted = ','.join(f'"{n}"' for n in names)
                c.execute(f'INSERT INTO {OWNER}.{TABLE} ({quoted}) VALUES ({",".join(":" + k for k in binds)})', binds)
                c.execute(f'SELECT {quoted} FROM {OWNER}.{TABLE} WHERE ENTRY_ID=:id', id=vals['ENTRY_ID'])
                got = c.fetchone()
                if not got or not all(same(cols[n], vals[n], g) for n, g in zip(names, got)):
                    raise ValueError(f'{TABLE}: a saved row did not read back the same.')
                inserted.append(dict(table=TABLE, key=op['key'], id=vals['ENTRY_ID'], values={n: plain(g) for n, g in zip(names, got)}))
                if not any(x['ordmasid'] == vals['ORDER_KEY'] for x in outorders):
                    outorders.append(dict(order_ref=vals.get('IONO') or '(no OC number)', ordmasid=vals['ORDER_KEY']))
            result = dict(job_id=draft['id'], mode='table', table=TABLE, orders=outorders, rows=inserted,
                          committed_at=datetime.now().astimezone().isoformat(), already_committed=False)
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                      job=draft['id'], filehash=draft['filehash'], hash=expected_hash, who=approver, payload=json.dumps(result, ensure_ascii=False))
            conn.commit()
        except Exception:
            conn.rollback(); raise
    with connect() as conn:
        c = conn.cursor(); bad = 0
        for row in result['rows']:
            names = list(row['values']); quoted = ','.join(f'"{n}"' for n in names)
            c.execute(f'SELECT {quoted} FROM {OWNER}.{TABLE} WHERE ENTRY_ID=:id', id=row['id'])
            got = c.fetchone()
            if not got or any(plain(g) != row['values'][n] for n, g in zip(names, got)):
                bad += 1
        result['verified'] = bad == 0; result['verified_rows'] = len(result['rows']) - bad
    return result


# ---------------------------------------------------------------- saved orders

def order_rows(conn, order_key):
    c = conn.cursor()
    c.execute(f'SELECT * FROM {OWNER}.{TABLE} WHERE ORDER_KEY=:k ORDER BY ORDSTYLEROW, ORDSIZEROW, ENTRY_ID', k=order_key)
    names = [d[0] for d in c.description]
    rows = [dict(zip(names, map(plain, r))) for r in c.fetchall()]
    if not rows:
        raise KeyError('Order not found')
    return rows


def summarize(rows):
    """Dashboard figures from the rows of one order (sizes and lines summed separately, nothing double counted)."""
    head = rows[0]; sizes = {}; lines = {}
    for r in rows:
        if r.get('SIZES') is not None:
            sizes[r['SIZES']] = sizes.get(r['SIZES'], 0) + (r.get('ORDERQTY') or 0)
        k = (r.get('ORDSTYLEROW'), r.get('COMBO'))
        if r.get('SIZES') is not None:
            line = lines.setdefault(k, dict(label=r.get('COMBO') or f'Line {r.get("ORDSTYLEROW")}', garment=r.get('GARMENT'), quantity=0,
                                            ship_date=r.get('SHIPDT'), sizes=[]))
            line['quantity'] += r.get('ORDERQTY') or 0; line['sizes'].append(dict(size=r['SIZES'], quantity=r.get('ORDERQTY')))
    size_total = sum(sizes.values())
    return dict(order_ref=head.get('IONO'), buyer=head.get('BUYERNAME'), style=head.get('STYLENO'), description=head.get('STYLEDESC'),
                po=head.get('PONUM'), order_date=head.get('ORDERDATE'), total=head.get('OQTY') if head.get('OQTY') is not None else size_total,
                size_total=size_total, reconciled=head.get('OQTY') is None or head.get('OQTY') == size_total,
                ship_dates=sorted({str(r['SHIPDT']) for r in rows if r.get('SHIPDT')}), sizes=[dict(size=k, quantity=v) for k, v in sizes.items()],
                lines=list(lines.values()), records={TABLE: len(rows)},
                filled={TABLE: sorted({k for r in rows for k, v in r.items() if v is not None})})


EDIT_LOCKED = {'ENTRY_ID', 'ORDER_KEY', 'IMPORT_ID', 'SOURCE_FILE', 'SOURCE_CELL', 'CREATEDBY', 'CREATEDON'}


def preview_changes(conn, order_key, changes):
    """Edits of a saved order, checked against the current rows. Nothing is written here.
    A change with id 'all' applies to every row of the order (order details repeat on each row)."""
    cols = columns(conn); rows = order_rows(conn, order_key); errors = []; out = []; snapshot = {}
    by_id = {r['ENTRY_ID']: r for r in rows}; proposed = {r['ENTRY_ID']: dict(r) for r in rows}
    if not changes:
        errors.append('No changes to review.')
    for ch in changes[:500]:
        col = str(ch.get('column') or '').upper(); value = ch.get('value')
        if col not in cols or not IDENT.match(col) or col in EDIT_LOCKED:
            errors.append(f'{col}: not editable.'); continue
        targets = list(by_id) if ch.get('id') == 'all' else [ch.get('id')]
        if any(t not in by_id for t in targets):
            errors.append(f'Row {ch.get("id")} does not belong to this order.'); continue
        if blank(value):
            new = None
        else:
            try:
                new = convert(cols[col], value)
                if isinstance(new, str) and cols[col]['type'] == 'NUMBER':
                    new = plain(Decimal(new))
            except ValueError as exc:
                errors.append(f'{cols[col]["label"]}: {exc}.'); continue
        for t in targets:
            proposed[t][col] = new
    before = sum(r.get('ORDERQTY') or 0 for r in rows); after = sum(p.get('ORDERQTY') or 0 for p in proposed.values())
    notes = []
    if before != after:
        if all(r.get('OQTY') == before for r in rows):
            for p in proposed.values():
                p['OQTY'] = after
            notes.append(f'Total pieces (OQTY) is recalculated from the sizes: {before} → {after}.')
        else:
            notes.append('OQTY did not equal the sum of the sizes before, so it is left unchanged.')
    for rid, p in proposed.items():
        for col, new in p.items():
            old = by_id[rid][col]
            if old != new:
                snapshot.setdefault(f'{TABLE}:{rid}', {})[col] = old
                out.append(dict(table=TABLE, id=rid, column=col, old=old, new=new, derived=col == 'OQTY' and not any(
                    str(ch.get('column', '')).upper() == 'OQTY' for ch in changes), pk='ENTRY_ID', group='ORDER_KEY', label=cols[col]['label']))
    if not out and not errors:
        errors.append('The proposed values are the same as Oracle. Nothing to change.')
    result = dict(ordmasid=order_key, changes=out, errors=list(dict.fromkeys(errors)), notes=notes, snapshot=snapshot)
    result['hash'] = digest(result)
    return result
