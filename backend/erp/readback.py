"""Build the post-save table flowchart from a fresh Oracle read, never from the receipt alone."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

NUMERIC = ('NUMBER', 'FLOAT', 'INTEGER', 'BINARY_FLOAT', 'BINARY_DOUBLE', 'BINARY_INTEGER')
PADDED = ('CHAR', 'NCHAR')      # Oracle blank-pads these to their length


def _moment(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return str(v)


def _is_number(v):
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def same(a, b, kind=None):
    """Is the value Oracle holds the value that was written? By the column's data type (all_tab_columns DATA_TYPE):
    numbers by value, CHAR text without Oracle's blank padding, other text exactly, DATE / TIMESTAMP as moments.
    Without a type: numbers by value and anything else exactly. Text is never compared as a number (“0042” is not
    “42”, “NAN123” equals itself) nor by its first characters only."""
    a, b = (v.read() if hasattr(v, 'read') else v for v in (a, b))
    if a is None or b is None:
        return a is None and b is None
    kind = str(kind or '').upper()
    if kind == 'DATE' or kind.startswith('TIMESTAMP'):
        return _moment(a) == _moment(b)
    if kind in NUMERIC or (not kind and _is_number(a) and _is_number(b)):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return False
    if kind in PADDED:
        return str(a).rstrip(' ') == str(b).rstrip(' ')
    if kind or isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    return a == b


_same = same   # the earlier private name


def _ref(value):
    """('ref', key) for a planned row's generated ID ({'ref': key}, see planner.Ref), else the value itself."""
    return ('ref', value['ref']) if isinstance(value, dict) and 'ref' in value else value


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
    types = receipt.get('column_types') or {}     # {table: {column: DATA_TYPE}}; receipts saved before it compare by value
    for table, g in groups.items():
        live = read(table, g['key'], [r['pk'] for r in g['rows']])
        kinds = types.get(table) or {}
        records = []
        for r in g['rows']:
            row = live.get(r['pk'])
            mismatched = [] if row is None else [c for c, v in r['values'].items() if not same(row.get(c), v, kinds.get(c))]
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
    return dict(job_id=receipt.get('job_id'), committed_at=receipt.get('committed_at'), verified=verified and bool(nodes),
                nodes=nodes, edges=edges, orders=receipt.get('orders', []))


def planned(plan, mapping):
    """The same graph shape as ``graph`` but built from an unsaved plan (approval screen).
    Generated IDs stay {'ref': key} references (the screen shows "new ID on save"); nothing here has been written."""
    masters = {m['table']: m for m in mapping['masters'].values()}
    labels = {}
    for op in plan.get('operations', []):
        if op['table'] in masters:
            m = masters[op['table']]
            labels[(op['table'], _ref(op['values'].get(m['key'])))] = op['values'].get(m['label'])
            labels[(op['table'], ('ref', op['key']))] = op['values'].get(m['label'])
    groups = {}
    for op in plan.get('operations', []):
        groups.setdefault(op['table'], []).append(op)
    nodes = []
    for table, ops in groups.items():
        spec = mapping['tables'].get(table)
        refs = _refs(mapping, spec) if spec else {}
        records = []
        for op in ops:
            decoded = {c: labels[(t, _ref(op['values'].get(c)))] for c, t in refs.items() if (t, _ref(op['values'].get(c))) in labels}
            records.append(dict(pk=op['key'], kind='reused' if op['action'] == 'reuse' else 'written', where=op.get('where'),
                                found=True, matches=True, mismatched=[], written_columns=sorted(op['values']) if op['action'] != 'reuse' else [],
                                live=op['values'], decoded=decoded, source=op.get('source') or {}, path=op.get('path')))
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
    return dict(planned=True, verified=None, nodes=nodes, edges=edges)
