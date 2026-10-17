"""Build the post-save table flowchart from a fresh Oracle read, never from the receipt alone."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a)[:10] == str(b)[:10] if len(str(b)) == 10 else str(a) == str(b)


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
                          verified=all(r['matches'] for r in records), records=records))
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
        refs = {c: mapping['masters'][x['master']]['table'] for c, x in spec['columns'].items() if x.get('master')}
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
    return dict(job_id=receipt.get('job_id'), committed_at=receipt.get('committed_at'), verified=verified and bool(nodes),
                nodes=nodes, edges=edges, orders=receipt.get('orders', []))
