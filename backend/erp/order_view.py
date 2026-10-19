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
