"""Build the post-save table flowchart from a fresh Oracle read, never from the receipt alone."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from . import order_numbers


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a)[:10] == str(b)[:10] if len(str(b)) == 10 else str(a) == str(b)


def _refs(mapping, spec):
    """Reference columns of a table spec -> master table, including copies of a parent's master column."""
    out = {}
    for c, x in spec['columns'].items():
        role = x.get('master')
        if not role and x.get('copy'):
            st, _, sc = x['copy'].partition('.')
            role = mapping['tables'].get(st, {}).get('columns', {}).get(sc, {}).get('master')
        if role:
            out[c] = mapping['masters'][role]['table']
    return out


def _labels(mapping, table):
    spec = mapping['tables'].get(table)
    if spec:
        return {c: x.get('label') or c for c, x in spec['columns'].items()}
    m = next((m for m in mapping['masters'].values() if m['table'] == table), None)
    return {m['key']: f'{m["business"]} ID', m['label']: m['business']} if m else {}


def graph(receipt, mapping, read):
    """``read(table, pk_column, ids) -> {id: full_row}`` performs the live query."""
    masters = {m['table']: (role, m) for role, m in mapping['masters'].items()}
    groups = {}
    for kind, rows in (('written', receipt.get('rows', [])), ('reused', receipt.get('reused', []))):
        for r in rows:
            table = r['table']
            key = mapping['tables'][table]['key'] if table in mapping['tables'] else masters[table][1]['key']
            pk = r.get('pk', r['values'].get(key))
            groups.setdefault(table, dict(key=key, rows=[]))['rows'].append(dict(pk=pk, kind=kind, values=r['values'], where=r.get('where')))
    nodes = []; verified = True
    for table, g in groups.items():
        live = read(table, g['key'], [r['pk'] for r in g['rows']])
        records = []
        for r in g['rows']:
            row = live.get(r['pk'])
            mismatched = [] if row is None else [c for c, v in r['values'].items() if not _same(row.get(c), v)]
            ok = row is not None and not mismatched
            verified &= ok
            records.append(dict(pk=r['pk'], kind=r['kind'], where=r.get('where'), found=row is not None, matches=ok,
                                mismatched=mismatched, written_columns=sorted(r['values']) if r['kind'] == 'written' else [],
                                live=row or {}))
        spec = mapping['tables'].get(table)
        nodes.append(dict(table=table, business=spec['business'] if spec else masters[table][1]['business'] + ' master',
                          screen=spec.get('screen') if spec else 'Masters', kind='master' if table in masters else
                          ('header' if spec.get('level') == 'order' else 'detail'), key=g['key'],
                          inserted=sum(r['kind'] == 'written' for r in records),
                          reused=sum(r['kind'] == 'reused' for r in records),
                          verified=all(r['matches'] for r in records), records=records, labels=_labels(mapping, table)))
    # Decode master references (e.g. COLOUR 1000000000005 → WHITE) from the master rows read back.
    labels = {}
    for n in nodes:
        if n['table'] in masters:
            col = masters[n['table']][1]['label']
            labels[n['table']] = {r['pk']: r['live'].get(col) for r in n['records']}
    for n in nodes:
        spec = mapping['tables'].get(n['table'])
        if not spec:
            continue
        refs = _refs(mapping, spec)
        for r in n['records']:
            r['decoded'] = {c: labels[t][r['live'][c]] for c, t in refs.items()
                            if t in labels and r['live'].get(c) in labels[t]}
    present = {n['table'] for n in nodes}
    edges = [dict(source=l['from'], target=l['to'], label=l['label'], on=l['on'])
             for l in mapping.get('links', []) if l['from'] in present and l['to'] in present]
    for table, spec in mapping['tables'].items():
        if table not in present:
            continue
        for col, c in spec['columns'].items():
            if c.get('master'):
                mt = mapping['masters'][c['master']]['table']
                if mt in present and not any(e['source'] == mt and e['target'] == table and e['on'] == col for e in edges):
                    edges.append(dict(source=mt, target=table, label=c['label'], on=col))
    controls = control_totals(receipt, nodes, mapping)
    return dict(job_id=receipt.get('job_id'), committed_at=receipt.get('committed_at'),
                verified=verified and bool(nodes) and all(c['ok'] for c in controls), controls=controls,
                nodes=nodes, edges=edges, orders=receipt.get('orders', []), sheets=order_sheets(nodes, mapping))


def order_sheets(nodes, mapping):
    """Each saved order as a person reads it: the header fields under their business names, and a colour × size table
    with totals. Every value comes from the rows Oracle returned on this read (masters shown by name), never from the
    save receipt. Tables and columns are found through the write mapping (level and business field), not by name."""
    tables = mapping['tables']
    level = {spec.get('level'): t for t, spec in tables.items() if spec.get('level') in ('order', 'line', 'size')}
    by_table = {n['table']: n for n in nodes}
    head_t, line_t, size_t = level.get('order'), level.get('line'), level.get('size')
    if head_t not in by_table:
        return []

    def col(table, field=None, parent=None):
        cols = (tables.get(table) or {}).get('columns', {})
        return next((c for c, x in cols.items() if (field and x.get('field') == field) or (parent and x.get('parent') == parent)), None)

    def shown(r, c):
        v = (r.get('decoded') or {}).get(c, (r.get('live') or {}).get(c))
        return _plain(v)

    def rows(table, parent_col, pk):
        # Saved rows hold the parent's ID; an unsaved plan holds a reference to the parent operation ("$key").
        return [r for r in (by_table.get(table) or {}).get('records', [])
                if parent_col and (r.get('live') or {}).get(parent_col) in (pk, f'${pk}')]

    out = []
    for h in by_table[head_t]['records']:
        if not h.get('found'):
            continue
        spec = tables[head_t]['columns']
        header = [dict(label=x.get('label') or c, value=shown(h, c), column=f'{head_t}.{c}')
                  for c, x in spec.items() if not x.get('key') and c in (h.get('written_columns') or []) and c in (h.get('live') or {})]
        ref_col = col(head_t, 'order.order_ref')
        lines = rows(line_t, col(line_t, parent=head_t), h['pk'])
        sizes_rows = rows(size_t, col(size_t, parent=head_t), h['pk'])
        l_link, s_link = col(line_t, 'calc.subno'), col(size_t, 'calc.subno')
        l_row, s_row = col(line_t, 'calc.row'), col(size_t, 'calc.row')
        colour, l_qty, ship = col(line_t, 'line.color'), col(line_t, 'calc.pieces'), col(line_t, 'order.ship_date')
        size_c, s_qty = col(size_t, 'size.size'), col(size_t, 'calc.pieces')
        order_sizes = []
        for r in sorted(sizes_rows, key=lambda r: _num((r.get('live') or {}).get(s_row)) or 0):
            name = str(shown(r, size_c) or '')
            if name not in order_sizes:
                order_sizes.append(name)
        sheet_lines = []
        for r in sorted(lines, key=lambda r: _num((r.get('live') or {}).get(l_row)) or 0):
            link = (r.get('live') or {}).get(l_link)
            qty = {}
            for x in sizes_rows:
                if (x.get('live') or {}).get(s_link) == link:
                    name = str(shown(x, size_c) or '')
                    qty[name] = (qty.get(name) or 0) + (_num((x.get('live') or {}).get(s_qty)) or 0)
            sheet_lines.append(dict(colour=shown(r, colour), ship_date=shown(r, ship), quantity=_plain(_num((r.get('live') or {}).get(l_qty))),
                                    sizes={k: _plain(v) for k, v in qty.items()}, total=_plain(sum(qty.values()))))
        size_totals = {s: _plain(sum(_num(l['sizes'].get(s)) or 0 for l in sheet_lines)) for s in order_sizes}
        own = [h] + lines + sizes_rows
        out.append(dict(order_ref=shown(h, ref_col) if ref_col else None, ordmasid=h['pk'], verified=all(r.get('matches') for r in own),
                        header=header, sizes=order_sizes, lines=sheet_lines, size_totals=size_totals,
                        total=_plain(sum(_num(v) or 0 for v in size_totals.values()))))
    return out


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if hasattr(v, 'isoformat'):
        return v.isoformat()[:10]
    return v


def control_totals(receipt, nodes, mapping):
    """Totals recomputed from the rows Oracle returned, against the approved plan's totals: pieces (sum of the size
    quantities) and the number of order headers, lines and size rows. A missing or extra row, or a changed quantity,
    fails the verification even when every row read back matches its own receipt values."""
    totals = receipt.get('totals') or {}
    live = {n['table']: [r['live'] for r in n['records'] if r['kind'] == 'written' and r['found']] for n in nodes}
    levels = {spec.get('level'): t for t, spec in mapping['tables'].items()}
    out = []
    size_table = levels.get('size')
    qty_col = next((c for c, x in mapping['tables'].get(size_table, {}).get('columns', {}).items()
                    if x.get('field') == 'calc.pieces' and x.get('status') == 'verified'), None) if size_table else None
    if size_table and qty_col and totals.get('pieces') is not None:
        found = sum(_num(r.get(qty_col)) for r in live.get(size_table, []))
        found = int(found) if found == found.to_integral_value() else float(found)
        out.append(dict(check='Pieces (sum of the size quantities)', expected=totals['pieces'], found=found, ok=found == totals['pieces']))
    for level, key in (('order', 'orders'), ('line', 'lines')):
        t = levels.get(level)
        if t and totals.get(key) is not None:
            found = len(live.get(t, []))
            out.append(dict(check=f'{key.capitalize()} saved', expected=totals[key], found=found, ok=found == totals[key]))
    return out


def _num(v):
    try:
        return Decimal(str(v)) if v is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _unsaved(value):
    """A value as the approval screen shows it: the stand-in of an OC number not given yet reads as words."""
    if isinstance(value, str) and 'AUTO-OC#' in value:
        return order_numbers.MARK.sub('(OC No. given on save)', value)
    return value


def planned(plan, mapping):
    """The same graph shape as ``graph`` but built from an unsaved plan (approval screen).
    Generated IDs are shown as '$key' references; nothing here has been written."""
    masters = {m['table']: m for m in mapping['masters'].values()}
    labels = {}
    for op in plan.get('operations', []):
        if op['table'] in masters:
            m = masters[op['table']]
            labels[(op['table'], op['values'].get(m['key']))] = op['values'].get(m['label'])
            labels[(op['table'], '$' + op['key'])] = op['values'].get(m['label'])
    groups = {}
    for op in plan.get('operations', []):
        groups.setdefault(op['table'], []).append(op)
    nodes = []
    for table, ops in groups.items():
        spec = mapping['tables'].get(table)
        refs = _refs(mapping, spec) if spec else {}
        records = []
        for op in ops:
            decoded = {c: labels[(t, op['values'].get(c))] for c, t in refs.items() if (t, op['values'].get(c)) in labels}
            records.append(dict(pk=op['key'], kind='reused' if op['action'] == 'reuse' else 'written', where=op.get('where'),
                                found=True, matches=True, mismatched=[], written_columns=sorted(op['values']) if op['action'] != 'reuse' else [],
                                live={c: _unsaved(v) for c, v in op['values'].items()}, decoded=decoded, source=op.get('source') or {},
                                path=op.get('path')))
        nodes.append(dict(table=table, business=spec['business'] if spec else masters[table]['business'] + ' master',
                          screen=spec.get('screen') if spec else 'Masters',
                          kind='master' if table in masters else ('header' if spec.get('level') == 'order' else 'detail'),
                          key=(spec or {}).get('key') or masters.get(table, {}).get('key'),
                          inserted=sum(r['kind'] == 'written' for r in records), reused=sum(r['kind'] == 'reused' for r in records),
                          verified=True, planned=True, records=records, labels=_labels(mapping, table)))
    for r in plan.get('reference_only', []):
        spec = mapping['tables'].get(r['table'], {})
        nodes.append(dict(table=r['table'], business=r['business'], screen=spec.get('screen'), kind='detail', key=spec.get('key'),
                          inserted=0, reused=0, verified=True, planned=True, reference_only=True, reference_rows=r['rows'],
                          reason=r['reason'], records=[], labels=_labels(mapping, r['table'])))
    present = {n['table'] for n in nodes}
    edges = [dict(source=l['from'], target=l['to'], label=l['label'], on=l['on'])
             for l in mapping.get('links', []) if l['from'] in present and l['to'] in present]
    for table, spec in mapping['tables'].items():
        if table not in present:
            continue
        for col, c in spec['columns'].items():
            if c.get('master'):
                mt = mapping['masters'][c['master']]['table']
                if mt in present and not any(e['source'] == mt and e['target'] == table and e['on'] == col for e in edges):
                    edges.append(dict(source=mt, target=table, label=c['label'], on=col))
    return dict(planned=True, verified=None, nodes=nodes, edges=edges, sheets=order_sheets(nodes, mapping))
