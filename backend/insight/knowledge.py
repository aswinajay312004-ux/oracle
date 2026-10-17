"""Knowledge catalog: one merged description of every visible table, column and relationship.

Layers, later ones win:
  1. physical  - live data dictionary (columns, types, row statistics, comments, declared FKs)
                 or the committed snapshot when Oracle is offline (marked live=False);
  2. registry  - the write-mapping registry (business labels, master references);
  3. curated   - config/semantic_catalog.json (business names, synonyms, roles, relationships);
  4. admin     - edits made in Setup -> Data dictionary (stored locally, audited).

Relationships carry a status: verified (curated/registry with evidence), declared (Oracle FK),
proposed (curated but unproven) or inferred (naming convention only). The question engine uses all
but rejected ones and reports in every answer which kind of link it relied on.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import re
from ..config import ROOT

CURATED = ROOT / 'config' / 'semantic_catalog.json'
IDENT = re.compile(r'^[A-Z][A-Z0-9_$#]{0,127}$')
STATUS_RANK = {'verified': 0, 'declared': 1, 'proposed': 2, 'inferred': 3}


def curated():
    return json.loads(CURATED.read_text(encoding='utf-8'))


def humanize(name):
    words = re.sub(r'[_$#]+', ' ', name).strip().lower()
    return words[:1].upper() + words[1:] if words else name


def norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-z/\-\. ]+', ' ', str(text or '').lower())).strip()


def _join_id(child, parent, pairs):
    return f'{child}->{parent}:' + ','.join(f'{a}={b}' for a, b in pairs)


def build(physical, *, mapping=None, curated_data=None, overrides=None, stats=None, comments=None, fks=None, live=False):
    """physical: {table: {column: {type, length, precision, scale, nullable}}} (column order preserved).
    stats: {table: num_rows}; comments: {'T': text, 'T.C': text}; fks: [{child, parent, pairs}]."""
    cur = curated_data if curated_data is not None else curated()
    mapping = mapping or {'masters': {}, 'tables': {}}
    overrides = overrides or {}
    stats = stats or {}; comments = comments or {}
    system = set(cur.get('system_columns', []))
    ctables = cur.get('tables', {})
    master_of = {m['table']: m for m in mapping['masters'].values()}
    tables = {}
    for name, cols in physical.items():
        if not IDENT.match(name):
            continue
        ct = ctables.get(name, {}); reg = mapping['tables'].get(name, {}); ms = master_of.get(name)
        key = reg.get('key') or (ms or {}).get('key') or (f'{name}ID' if f'{name}ID' in cols else None)
        kind = ct.get('kind') or ('master' if ms else 'header' if name == 'ORDMAS' else 'detail' if reg else
                                  'master' if name.endswith('MAS') and key else 'other')
        label = ct.get('label') or (ms or {}).get('label')
        columns = {}
        for col, meta in cols.items():
            if not IDENT.match(col):   # names that would need quoting are never used in generated SQL
                continue
            cc = ct.get('columns', {}).get(col, {}); rc = reg.get('columns', {}).get(col, {})
            role = cc.get('role')
            if not role:
                if col == key or 'parent' in rc:
                    role = 'key'
                elif col in system:
                    role = 'system'
                elif rc.get('master'):
                    role = 'reference'
                elif meta['type'] == 'DATE' or meta['type'].startswith('TIMESTAMP'):
                    role = 'date'
                else:
                    role = 'attribute'
            if col == label and role == 'attribute':
                role = 'label'
            columns[col] = dict(name=col, type=meta['type'], length=meta.get('length'), precision=meta.get('precision'),
                                scale=meta.get('scale'), nullable=meta.get('nullable'),
                                business=cc.get('business') or rc.get('label') or comments.get(f'{name}.{col}') or humanize(col),
                                description=comments.get(f'{name}.{col}') or cc.get('description') or '',
                                synonyms=list(cc.get('synonyms', [])), role=role, unit=cc.get('unit'),
                                searchable=bool(cc.get('searchable') or role == 'label'), short_values=bool(cc.get('short_values')),
                                references=None, tags=[], status='verified' if (cc or rc.get('status') == 'verified') else 'unreviewed',
                                curated=bool(cc or rc))
        tables[name] = dict(name=name, business=ct.get('business') or (ms or {}).get('business') or reg.get('business') or humanize(name),
                            plural=ct.get('plural') or (ct.get('business') or humanize(name)).lower() + 's',
                            description=comments.get(name) or ct.get('description') or reg.get('business') or '',
                            synonyms=list(ct.get('synonyms', [])), kind=kind, key=key, label=label if label in cols else None,
                            date=ct.get('date') if ct.get('date') in cols else None,
                            display=[c for c in ct.get('display', []) if c in cols], rows=stats.get(name),
                            tags=[], status='verified' if ct else 'unreviewed', curated=bool(ct), columns=columns)

    joins = {}

    def add_join(child, parent, pairs, label, status, source):
        if child not in tables or parent not in tables:
            return
        if not all(a in tables[child]['columns'] and b in tables[parent]['columns'] for a, b in pairs):
            return
        jid = _join_id(child, parent, pairs)
        old = joins.get(jid)
        if old and STATUS_RANK[old['status']] <= STATUS_RANK[status]:
            return
        joins[jid] = dict(id=jid, child=child, parent=parent, pairs=[list(p) for p in pairs], label=label, status=status, source=source)

    for r in cur.get('relationships', []):
        add_join(r['child'], r['parent'], r['pairs'], r.get('label', ''), r.get('status', 'proposed'), 'curated')
    for tname, spec in mapping['tables'].items():
        for col, c in spec['columns'].items():
            if c.get('master'):
                m = mapping['masters'][c['master']]
                ok = c['status'] == 'verified' and spec['status'] == 'verified'
                add_join(tname, m['table'], [[col, m['key']]], f'{c.get("label") or col} ({tname}.{col})',
                         'verified' if ok else 'proposed', 'registry')
    for fk in fks or []:
        add_join(fk['child'], fk['parent'], fk['pairs'], f'{fk["child"]} → {fk["parent"]} (database constraint)', 'declared', 'constraint')
    keys = {t['key']: n for n, t in tables.items() if t['key']}
    for name, t in tables.items():
        for col, c in t['columns'].items():
            parent = keys.get(col)
            if parent and c['role'] == 'attribute':
                c['role'] = 'key'
            if parent and parent != name and c['role'] != 'system':
                add_join(name, parent, [[col, col]], f'{name}.{col} → {parent} (same column name)', 'inferred', 'naming')

    for jid, j in joins.items():
        ov = overrides.get('join:' + jid)
        if ov and ov.get('status') in ('verified', 'proposed', 'rejected'):
            j['status'] = ov['status']
    for j in joins.values():   # single-column reference to a master: mark the column
        if len(j['pairs']) == 1 and tables[j['parent']]['kind'] == 'master' and j['status'] != 'rejected':
            col = tables[j['child']]['columns'][j['pairs'][0][0]]
            if col['role'] in ('attribute', 'reference', 'key') and col['name'] != tables[j['child']]['key']:
                if not col['references'] or STATUS_RANK.get(j['status'], 9) < STATUS_RANK.get(col.get('ref_status'), 9):
                    col['references'] = j['parent']; col['ref_status'] = j['status']
                    if col['role'] == 'attribute':
                        col['role'] = 'reference'

    for obj, ov in overrides.items():   # admin edits: business name, synonyms, description, tags, role
        if obj.startswith('join:'):
            continue
        t, _, c = obj.partition('.')
        target = tables.get(t) if not c else tables.get(t, {}).get('columns', {}).get(c)
        if not target:
            continue
        for k in ('business', 'description', 'role', 'status'):
            if ov.get(k):
                target[k] = ov[k]
        for k in ('synonyms', 'tags'):
            if isinstance(ov.get(k), list):
                target[k] = [str(x)[:60] for x in ov[k]][:30]
        if c and 'searchable' in ov:
            target['searchable'] = bool(ov['searchable'])
    for t in tables.values():
        if not t['label']:
            t['label'] = next((c for c, m in t['columns'].items() if m['role'] == 'label'), None)
        if not t['display']:
            t['display'] = [c for c, m in t['columns'].items() if m['role'] in ('label', 'measure', 'date', 'reference', 'attribute')][:6]
    return dict(version=1, generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'), live=live,
                tables=tables, joins=sorted(joins.values(), key=lambda j: j['id']))


class Knowledge:
    """Graph and lookup helpers over a built catalog."""

    def __init__(self, data):
        self.data = data
        self.tables = data['tables']
        self.joins = [j for j in data['joins'] if j['status'] != 'rejected']
        self.live = data.get('live', False)

    def table(self, name):
        return self.tables.get(name)

    def column(self, ref):
        t, _, c = ref.partition('.')
        return self.tables.get(t, {}).get('columns', {}).get(c)

    def neighbours(self, table):
        out = []
        for j in self.joins:
            if j['child'] == table:
                out.append(dict(table=j['parent'], join=j, direction='up'))
            elif j['parent'] == table:
                out.append(dict(table=j['child'], join=j, direction='down'))
        return out

    def path(self, start, goal, prefer_up=True):
        """Shortest join path start -> goal. Each step: (join, direction). Prefers verified links and
        many-to-one ('up') steps, so filters and labels never multiply the base rows when avoidable."""
        if start == goal:
            return []
        best = {start: (0, 0, [])}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            cost, down, steps = best[node]
            for n in self.neighbours(node):
                if any(s[0]['id'] == n['join']['id'] for s in steps):
                    continue
                c = cost + 10 + STATUS_RANK[n['join']['status']] * 3 + (6 if n['direction'] == 'down' and prefer_up else 0)
                d = down + (n['direction'] == 'down')
                if n['table'] not in best or (c, d) < best[n['table']][:2]:
                    best[n['table']] = (c, d, steps + [(n['join'], n['direction'])])
                    queue.append(n['table'])
        return best[goal][2] if goal in best else None

    def summary(self):
        return dict(tables=len(self.tables), joins=len(self.joins), live=self.live, generated_at=self.data.get('generated_at'),
                    statuses={s: sum(1 for j in self.joins if j['status'] == s) for s in STATUS_RANK})
