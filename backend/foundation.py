"""AI-ready data foundation: a local metadata store beside Oracle (never inside the client's business tables).

  catalog_tables / catalog_columns   table and column "ID cards": structure from Oracle, meaning from people
  schema_versions                    every detected change of the Oracle structure
  order_registry / record_links      one internal UUID per order, linked to every Oracle row it owns
  lineage                            for every saved value: Excel file, sheet, cell, raw value, saved value, approval
  field_status                       what a blank means: PROVIDED / NOT_PROVIDED / NOT_APPLICABLE / UNKNOWN / INVALID
  quality_results                    the checks that ran for an approval and the read-back outcome
  business_metrics                   versioned metric definitions; only APPROVED ones may be used by a future AI

Structure is discovered automatically; meaning is never invented. A discovered table stays DISCOVERED until a
person documents it, and only AI_READY tables (with verified joins and approved metrics) are offered to a future
question-answering agent. Oracle and this store are not one distributed transaction: lineage is written after the
Oracle commit from the durable receipt (PP_IMPORT_LOG), and record() is idempotent, so a crash between the two is
repaired by calling it again (reconcile())."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
import re
import uuid
from .config import OWNER
from .local_store import db

STATUSES = ['DISCOVERED', 'DOCUMENTED', 'RELATIONSHIPS_VERIFIED', 'BUSINESS_APPROVED', 'AI_READY']
NULL_STATES = ['PROVIDED', 'NOT_PROVIDED', 'NOT_APPLICABLE', 'UNKNOWN', 'INVALID']


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS catalog_tables(name TEXT PRIMARY KEY, schema TEXT, business_name TEXT, purpose TEXT, grain TEXT,
            domain TEXT, status TEXT NOT NULL DEFAULT 'DISCOVERED', classification TEXT DEFAULT 'BUSINESS', ai_searchable INTEGER DEFAULT 0,
            row_estimate INTEGER, structure_hash TEXT, first_seen TEXT, last_seen TEXT, removed_at TEXT, updated_by TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS catalog_columns(table_name TEXT, column_name TEXT, data_type TEXT, nullable TEXT, is_key INTEGER DEFAULT 0,
            business_name TEXT, description TEXT, unit TEXT, synonyms TEXT DEFAULT '[]', sensitive INTEGER DEFAULT 0, oracle_comment TEXT,
            first_seen TEXT, last_seen TEXT, removed_at TEXT, updated_by TEXT, updated_at TEXT, PRIMARY KEY(table_name, column_name));
        CREATE TABLE IF NOT EXISTS schema_versions(id INTEGER PRIMARY KEY, taken_at TEXT, fingerprint TEXT, tables INTEGER, columns INTEGER,
            added TEXT, removed TEXT, changed TEXT);
        CREATE TABLE IF NOT EXISTS order_registry(uuid TEXT PRIMARY KEY, job_id TEXT NOT NULL, order_id INTEGER NOT NULL, order_ref TEXT,
            mode TEXT, target_table TEXT, file_hash TEXT, filename TEXT, approved_by TEXT, approved_at TEXT, mapping_version INTEGER,
            template TEXT, UNIQUE(job_id, order_id));
        CREATE TABLE IF NOT EXISTS record_links(uuid TEXT, table_name TEXT, row_id INTEGER, PRIMARY KEY(uuid, table_name, row_id));
        CREATE TABLE IF NOT EXISTS lineage(id INTEGER PRIMARY KEY, uuid TEXT, table_name TEXT, row_id INTEGER, column_name TEXT,
            saved_value TEXT, raw_value TEXT, sheet TEXT, cell TEXT, formula TEXT, origin TEXT, via TEXT, transformed INTEGER,
            job_id TEXT, approved_by TEXT, verified INTEGER, at TEXT, UNIQUE(uuid, table_name, row_id, column_name));
        CREATE INDEX IF NOT EXISTS lineage_order ON lineage(uuid, column_name);
        CREATE TABLE IF NOT EXISTS field_status(uuid TEXT, column_name TEXT, status TEXT, note TEXT, PRIMARY KEY(uuid, column_name));
        CREATE TABLE IF NOT EXISTS quality_results(id INTEGER PRIMARY KEY, job_id TEXT, uuid TEXT, rule TEXT, outcome TEXT, detail TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS business_metrics(id INTEGER PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT,
            formula TEXT, source_tables TEXT, grain TEXT, filters TEXT, null_handling TEXT, duplicate_handling TEXT, status TEXT NOT NULL,
            created_by TEXT, created_at TEXT, approved_by TEXT, approved_at TEXT, UNIQUE(name, version));
        ''')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------ catalog

def sync(conn, actor='system', known=None):
    """Refresh table/column ID cards from Oracle's own dictionary. Human-written meaning is never overwritten.
    Returns the detected schema change (also stored as a schema version when anything changed)."""
    c = conn.cursor()
    c.execute('''select t.table_name, t.column_name, t.data_type, t.data_length, t.data_precision, t.data_scale, t.nullable, m.comments
        from all_tab_columns t left join all_col_comments m on m.owner=t.owner and m.table_name=t.table_name and m.column_name=t.column_name
        where t.owner=:o order by t.table_name, t.column_id''', o=OWNER)
    struct = {}
    for table, col, kind, length, precision, scale, nullable, comment in c:
        size = f'({precision},{scale or 0})' if kind == 'NUMBER' and precision else f'({length})' if 'CHAR' in kind else ''
        struct.setdefault(table, {})[col] = dict(type=kind + size, nullable=nullable, comment=comment)
    c.execute("select table_name, num_rows from all_tables where owner=:o", o=OWNER)
    rows = dict(c.fetchall())
    c.execute("select table_name, comments from all_tab_comments where owner=:o and comments is not null", o=OWNER)
    table_comments = dict(c.fetchall())
    c.execute('''select cc.table_name, cc.column_name from all_constraints k join all_cons_columns cc
        on cc.owner=k.owner and cc.constraint_name=k.constraint_name where k.owner=:o and k.constraint_type='P' ''', o=OWNER)
    keys = {(t, col) for t, col in c}
    known = known or {}
    stamp = now(); added, removed, changed = [], [], []
    with db() as s:
        before = {r['name']: r for r in s.execute('SELECT * FROM catalog_tables WHERE removed_at IS NULL')}
        for table, cols in struct.items():
            h = digest(cols)
            name, purpose, grain = known.get(table, (None, None, None))
            purpose = purpose or table_comments.get(table)
            if table not in before:
                added.append(table)
                s.execute('''INSERT INTO catalog_tables(name, schema, business_name, purpose, grain, status, row_estimate, structure_hash, first_seen, last_seen, updated_by, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET removed_at=NULL, structure_hash=excluded.structure_hash, last_seen=excluded.last_seen''',
                    (table, OWNER, name, purpose, grain, 'DOCUMENTED' if name and purpose and grain else 'DISCOVERED', rows.get(table), h, stamp, stamp, actor, stamp))
            else:
                if before[table]['structure_hash'] != h:
                    changed.append(table)
                s.execute('UPDATE catalog_tables SET structure_hash=?, last_seen=?, row_estimate=?, business_name=coalesce(business_name,?), purpose=coalesce(purpose,?), grain=coalesce(grain,?) WHERE name=?',
                          (h, stamp, rows.get(table), name, purpose, grain, table))
            for col, m in cols.items():
                labels = [x.strip() for x in (m['comment'] or '').split('|') if x.strip()]
                s.execute('''INSERT INTO catalog_columns(table_name, column_name, data_type, nullable, is_key, business_name, synonyms, oracle_comment, first_seen, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(table_name, column_name) DO UPDATE SET data_type=excluded.data_type, nullable=excluded.nullable,
                    is_key=excluded.is_key, oracle_comment=excluded.oracle_comment, last_seen=excluded.last_seen, removed_at=NULL,
                    business_name=coalesce(catalog_columns.business_name, excluded.business_name),
                    synonyms=CASE WHEN catalog_columns.synonyms IN ('[]','') THEN excluded.synonyms ELSE catalog_columns.synonyms END''',
                    (table, col, m['type'], m['nullable'], int((table, col) in keys), labels[0] if labels else None, json.dumps(labels[1:]), m['comment'], stamp, stamp))
            s.execute('UPDATE catalog_columns SET removed_at=? WHERE table_name=? AND removed_at IS NULL AND last_seen<>?', (stamp, table, stamp))
        for table in set(before) - set(struct):
            removed.append(table)
            s.execute('UPDATE catalog_tables SET removed_at=? WHERE name=?', (stamp, table))
        fingerprint = digest({t: digest(cols) for t, cols in struct.items()})
        last = s.execute('SELECT fingerprint FROM schema_versions ORDER BY id DESC LIMIT 1').fetchone()
        if not last or last['fingerprint'] != fingerprint:
            s.execute('INSERT INTO schema_versions(taken_at, fingerprint, tables, columns, added, removed, changed) VALUES (?,?,?,?,?,?,?)',
                      (stamp, fingerprint, len(struct), sum(len(x) for x in struct.values()), json.dumps(added), json.dumps(removed), json.dumps(changed)))
    return dict(tables=len(struct), added=added, removed=removed, changed=changed, fingerprint=fingerprint)


def tables(include_removed=False):
    with db() as s:
        q = 'SELECT t.*, (SELECT count(*) FROM catalog_columns c WHERE c.table_name=t.name AND c.removed_at IS NULL) AS columns FROM catalog_tables t'
        return [dict(r) for r in s.execute(q + ('' if include_removed else ' WHERE removed_at IS NULL') + ' ORDER BY name')]


def card(name, relationships=(), metrics=None):
    """The ID card of one table: identity, meaning, readiness, columns, verified joins and metrics."""
    with db() as s:
        t = s.execute('SELECT * FROM catalog_tables WHERE name=?', (name,)).fetchone()
        if not t:
            raise KeyError('Table not in the catalog. Refresh the catalog first')
        cols = [dict(r, synonyms=json.loads(r['synonyms'] or '[]')) for r in s.execute(
            'SELECT * FROM catalog_columns WHERE table_name=? AND removed_at IS NULL ORDER BY rowid', (name,))]
    rels = [dict(r, join_verified=r['kind'] in ('verified', 'physical')) for r in relationships if name in (r['parent'], r['child'])]
    return dict(dict(t), columns=cols, relationships=rels, metrics=[m for m in (metrics or []) if name in m['source_tables']],
                checklist=readiness(dict(t), cols, rels))


def readiness(t, cols, rels):
    """What is still missing before a table may be trusted by a future AI (shown, never auto-completed)."""
    return [dict(step='Business name and purpose written', done=bool(t.get('business_name') and t.get('purpose'))),
            dict(step='Grain written (what one row represents)', done=bool(t.get('grain'))),
            dict(step='Every column has a business name', done=all(c['business_name'] for c in cols) and bool(cols)),
            dict(step='Joins are verified (or the table stands alone)', done=all(r['join_verified'] for r in rels)),
            dict(step='Approved by the business owner', done=STATUSES.index(t.get('status') or 'DISCOVERED') >= STATUSES.index('BUSINESS_APPROVED'))]


def document(name, data, actor):
    """Human documentation of a table and its columns. Status moves one step at a time and only when its checks pass."""
    current = card(name)
    target = data.get('status') or current['status']
    if target not in STATUSES:
        raise ValueError('Unknown status.')
    fields = {k: str(data[k]).strip()[:2000] if data.get(k) is not None else None for k in ('business_name', 'purpose', 'grain', 'domain', 'classification') if k in data}
    merged = dict(current, **fields)
    done = {c['step']: c['done'] for c in readiness(merged, current['columns'], current['relationships'])}
    need = {'DOCUMENTED': ['Business name and purpose written', 'Grain written (what one row represents)'],
            'RELATIONSHIPS_VERIFIED': ['Joins are verified (or the table stands alone)'],
            'AI_READY': ['Every column has a business name', 'Approved by the business owner']}
    if STATUSES.index(target) > STATUSES.index(current['status']) + 1:
        raise ValueError('Move the status one step at a time.')
    if target == 'AI_READY' and current['status'] != 'BUSINESS_APPROVED':
        raise ValueError('A table must be business-approved before it is AI ready.')
    for step in need.get(target, []):
        if not done.get(step) and not (target == 'AI_READY' and step == 'Approved by the business owner'):
            raise ValueError(f'Before "{target.replace("_", " ").lower()}": {step.lower()}.')
    with db() as s:
        sets = dict(fields, status=target, ai_searchable=int(target == 'AI_READY'), updated_by=actor, updated_at=now())
        s.execute(f'UPDATE catalog_tables SET {", ".join(k + "=?" for k in sets)} WHERE name=?', (*sets.values(), name))
        for col in data.get('columns') or []:
            s.execute('''UPDATE catalog_columns SET business_name=?, description=?, unit=?, synonyms=?, sensitive=?, updated_by=?, updated_at=?
                WHERE table_name=? AND column_name=?''', (str(col.get('business_name') or '')[:200] or None, str(col.get('description') or '')[:2000] or None,
                str(col.get('unit') or '')[:40] or None, json.dumps([str(x)[:80] for x in col.get('synonyms') or []][:30]), int(bool(col.get('sensitive'))),
                actor, now(), name, col.get('column_name')))
    return card(name)


def schema_changes(limit=20):
    with db() as s:
        return [dict(r, added=json.loads(r['added']), removed=json.loads(r['removed']), changed=json.loads(r['changed']))
                for r in s.execute('SELECT * FROM schema_versions ORDER BY id DESC LIMIT ?', (limit,))]


WORD = re.compile(r'[a-z0-9]+')


def search(q, limit=20, ai_ready_only=False):
    """Metadata retrieval for people and a future AI: business words → tables, columns and metrics.
    Only metadata is searched; no business rows are read or embedded."""
    words = set(WORD.findall(q.lower()))
    if not words:
        return []
    hits = []
    with db() as s:
        for t in s.execute('SELECT * FROM catalog_tables WHERE removed_at IS NULL' + (" AND status='AI_READY'" if ai_ready_only else '')):
            text = ' '.join(str(t[k] or '') for k in ('name', 'business_name', 'purpose', 'grain', 'domain')).lower()
            score = sum(3 if w in WORD.findall(t['name'].lower().replace('_', ' ')) else 1 for w in words if w in text)
            if score:
                hits.append(dict(kind='table', table=t['name'], name=t['business_name'] or t['name'], status=t['status'], score=score + 1))
        for c in s.execute('SELECT c.*, t.status FROM catalog_columns c JOIN catalog_tables t ON t.name=c.table_name WHERE c.removed_at IS NULL'
                           + (" AND t.status='AI_READY'" if ai_ready_only else '')):
            text = ' '.join(str(c[k] or '') for k in ('column_name', 'business_name', 'description', 'synonyms', 'oracle_comment')).lower().replace('_', ' ')
            score = sum(1 for w in words if w in WORD.findall(text))
            if score:
                hits.append(dict(kind='column', table=c['table_name'], column=c['column_name'], name=c['business_name'] or c['column_name'],
                                 status=c['status'], score=score))
        for m in s.execute("SELECT * FROM business_metrics WHERE status='APPROVED'" if ai_ready_only else 'SELECT * FROM business_metrics'):
            text = f"{m['name']} {m['definition']}".lower()
            score = sum(1 for w in words if w in WORD.findall(text))
            if score:
                hits.append(dict(kind='metric', name=m['name'], version=m['version'], status=m['status'], score=score + 1))
    return sorted(hits, key=lambda h: -h['score'])[:limit]


# ------------------------------------------------------------------ identity, lineage, null semantics, quality

def _txt(v):
    return None if v is None else str(v)


def record(draft, receipt, actor):
    """After an Oracle commit: register each order's UUID, link its rows, store field-level lineage, what every blank
    means and the checks that ran. Idempotent (safe to repeat after a crash)."""
    plan = draft.get('plan') or {}; ops = {op['key']: op for op in plan.get('operations', [])}
    table_mode = receipt.get('mode') in ('table', 'entry')
    entry_mode = receipt.get('mode') == 'entry'
    with db() as s:
        for oi, o in enumerate(receipt.get('orders', [])):
            row = s.execute('SELECT uuid FROM order_registry WHERE job_id=? AND order_id=?', (receipt['job_id'], o['ordmasid'])).fetchone()
            oid = row['uuid'] if row else str(uuid.uuid4())
            s.execute('''INSERT OR IGNORE INTO order_registry(uuid, job_id, order_id, order_ref, mode, target_table, file_hash, filename, approved_by, approved_at, mapping_version, template)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (oid, receipt['job_id'], o['ordmasid'], o['order_ref'], receipt.get('mode', 'erp'), receipt.get('table'),
                draft.get('filehash'), draft.get('filename'), actor, receipt.get('committed_at'), draft.get('version'), json.dumps(draft.get('template'))))
            mine = [r for r in receipt['rows'] if (entry_mode and r.get('order') == o['ordmasid']) or (not entry_mode and r['values'].get('ORDER_KEY') == o['ordmasid'])
                    or (not table_mode and r['values'].get('ORDMASID') == o['ordmasid'])
                    or (not table_mode and r['table'] not in ('ORDMAS', 'ORDSTYLE', 'ORDSIZE'))]
            for r in mine:
                s.execute('INSERT OR IGNORE INTO record_links VALUES (?,?,?)', (oid, r['table'], r.get('id')))
                cols = (ops.get(r.get('key')) or {}).get('columns', {})
                for col, saved in r['values'].items():
                    info = cols.get(col) or {}; src = info.get('source') or {}
                    raw = info.get('raw', src.get('value'))
                    s.execute('''INSERT OR IGNORE INTO lineage(uuid, table_name, row_id, column_name, saved_value, raw_value, sheet, cell, formula, origin, via,
                        transformed, job_id, approved_by, verified, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (oid, r['table'], r.get('id'), col, _txt(saved), _txt(raw), src.get('sheet'), src.get('cell'), src.get('formula'), info.get('origin'),
                         src.get('via') or src.get('origin'), int(raw is not None and _txt(raw) != _txt(saved)), receipt['job_id'], actor,
                         int(bool(receipt.get('verified', True))), receipt.get('committed_at')))
            if table_mode:
                excel_order = (draft.get('orders') or [{}])[min(oi, len(draft.get('orders') or [{}]) - 1)]
                statuses = field_states(plan, excel_order, o['ordmasid'], mine)
                for col, (state, note) in statuses.items():
                    s.execute('INSERT OR REPLACE INTO field_status VALUES (?,?,?,?)', (oid, col, state, note))
            existing = s.execute('SELECT count(*) FROM quality_results WHERE job_id=? AND uuid=?', (receipt['job_id'], oid)).fetchone()[0]
            if not existing:
                have = bool(plan)
                checks = [('Validation before approval', ('PASS' if not plan.get('errors') else 'FAIL') if have else 'SKIP',
                           f"{len(plan.get('errors', []))} blocking, {len(plan.get('warnings', []))} to check, {len(plan.get('info', []))} notes" if have else 'The preview of this import is not in this workspace'),
                          ('Saved values read back from Oracle', 'PASS' if receipt.get('verified', True) else 'FAIL', f"{receipt.get('verified_rows', len(receipt['rows']))} of {len(receipt['rows'])} rows identical"),
                          ('Original workbook kept unchanged', 'PASS' if draft.get('filehash') else 'SKIP',
                           f"sha256 {str(draft.get('filehash'))[:16]}…" if draft.get('filehash') else 'The workbook is not in this workspace'),
                          ('Duplicate order check', 'PASS', f"OC number {o['order_ref']} was not saved before")]
                checks += [('Needs attention at approval', 'WARN', w) for w in plan.get('warnings', [])]
                for rule, outcome, detail in checks:
                    s.execute('INSERT INTO quality_results(job_id, uuid, rule, outcome, detail, at) VALUES (?,?,?,?,?,?)', (receipt['job_id'], oid, rule, outcome, detail, now()))


def field_states(plan, excel_order, order_key, rows):
    """PROVIDED: a value was saved. NOT_APPLICABLE: the reviewer said so. UNKNOWN: Excel has the label but no value.
    NOT_PROVIDED: nothing about it in Excel. An absent field is never turned into 'false' or 'not applicable'."""
    meta = (plan.get('tables') or {}).get(plan.get('table'), {}).get('columns', [])
    first = next((r['values'] for r in rows if r.get('table', plan.get('table')) == plan.get('table')), {}) if rows else {}
    chosen = excel_order.get('table_values') or {}
    for col, b in ((excel_order.get('bind') or {}).get(plan.get('table')) or {}).items():
        if b.get('kind') == 'na':
            chosen[col] = {'source': {'origin': 'Not applicable'}}
    labelled = set(plan.get('labelled_blank') or [])
    out = {}
    for c in meta:
        if c.get('level') != 'order':
            continue
        name = c['name']
        if first.get(name) is not None:
            out[name] = ('PROVIDED', None)
        elif (chosen.get(name) or {}).get('source', {}).get('origin') == 'Not applicable':
            out[name] = ('NOT_APPLICABLE', 'Marked not applicable by the reviewer')
        elif name in labelled:
            out[name] = ('UNKNOWN', 'The Excel has this label but the value cell is empty')
        else:
            out[name] = ('NOT_PROVIDED', 'Not in the Excel')
    return out


def order_profile(order_id):
    with db() as s:
        reg = s.execute('SELECT * FROM order_registry WHERE order_id=? ORDER BY approved_at LIMIT 1', (order_id,)).fetchone()
        if not reg:
            return None
        oid = reg['uuid']
        return dict(dict(reg), links=[dict(r) for r in s.execute('SELECT table_name, count(*) AS rows FROM record_links WHERE uuid=? GROUP BY table_name', (oid,))],
                    fields=[dict(r) for r in s.execute('SELECT column_name, status, note FROM field_status WHERE uuid=? ORDER BY rowid', (oid,))],
                    quality=[dict(r) for r in s.execute('SELECT rule, outcome, detail, at FROM quality_results WHERE uuid=? ORDER BY id', (oid,))])


def lineage(order_id, column=None, row_id=None):
    """Where a saved value came from (Excel sheet/cell/raw) and what happened to it since (approved edits)."""
    with db() as s:
        reg = s.execute('SELECT uuid FROM order_registry WHERE order_id=?', (order_id,)).fetchone()
        if not reg:
            return []
        q = 'SELECT * FROM lineage WHERE uuid=?'; args = [reg['uuid']]
        if column:
            q += ' AND column_name=?'; args.append(column)
        if row_id is not None:
            q += ' AND row_id=?'; args.append(row_id)
        return [dict(r) for r in s.execute(q + ' ORDER BY id LIMIT 2000', args)]


def value_history(order_id, column, changes):
    """Original (as approved from Excel), each approved change, and current: for one column of one order."""
    origin = lineage(order_id, column)
    steps = [dict(version=1, value=origin[0]['saved_value'], source=f"{origin[0]['sheet']}!{origin[0]['cell']}" if origin[0]['cell'] else origin[0]['origin'],
                  at=origin[0]['at'], by=origin[0]['approved_by'])] if origin else []
    seen = set()
    for ch in sorted((c for c in changes if c['column'] == column), key=lambda c: c['changed_at']):
        if ch['changeset'] in seen:
            continue
        seen.add(ch['changeset'])
        steps.append(dict(version=len(steps) + 1, value=ch['new'], previous=ch['old'], at=ch['changed_at'], by=ch['approver']))
    return steps


# ------------------------------------------------------------------ business metrics

def metrics(status=None):
    with db() as s:
        q = 'SELECT * FROM business_metrics' + (' WHERE status=?' if status else '') + ' ORDER BY name, version DESC'
        return [dict(r, source_tables=json.loads(r['source_tables'] or '[]')) for r in s.execute(q, (status,) if status else ())]


def propose_metric(data, actor):
    name = str(data.get('name') or '').strip()
    if not 2 <= len(name) <= 80 or not str(data.get('definition') or '').strip():
        raise ValueError('A metric needs a name and a written business definition.')
    tables_ = [str(t).upper() for t in data.get('source_tables') or []]
    with db() as s:
        version = (s.execute('SELECT max(version) FROM business_metrics WHERE name=?', (name,)).fetchone()[0] or 0) + 1
        s.execute('''INSERT INTO business_metrics(name, version, definition, formula, source_tables, grain, filters, null_handling, duplicate_handling,
            status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (name, version, data['definition'], data.get('formula'), json.dumps(tables_),
            data.get('grain'), data.get('filters'), data.get('null_handling'), data.get('duplicate_handling'), 'PROPOSED', actor, now()))
    return version


def approve_metric(metric_id, actor):
    with db() as s:
        m = s.execute('SELECT * FROM business_metrics WHERE id=?', (metric_id,)).fetchone()
        if not m:
            raise KeyError('Metric not found')
        s.execute("UPDATE business_metrics SET status='RETIRED' WHERE name=? AND status='APPROVED'", (m['name'],))
        s.execute("UPDATE business_metrics SET status='APPROVED', approved_by=?, approved_at=? WHERE id=?", (actor, now(), metric_id))


def seed_metrics(table, key='ORDER_KEY'):
    """Starting definitions for the single order table. They follow from how POPPYS writes that table, but they stay
    PROPOSED until the business owner approves them: the system never approves its own formulas."""
    with db() as s:
        if s.execute('SELECT count(*) FROM business_metrics').fetchone()[0]:
            return
    for m in [
        dict(name='Order quantity (pieces)', definition='Pieces ordered in one order.', formula=f'SUM(ORDERQTY) FROM {table} GROUP BY {key}',
             grain='one order', null_handling='Rows without a quantity are not counted.',
             duplicate_handling=f'Never SUM(OQTY): OQTY repeats on every row of an order. Use SUM(ORDERQTY), or MAX(OQTY) per {key}.'),
        dict(name='Total orders', definition='Number of saved orders.', formula=f'COUNT(DISTINCT {key}) FROM {table}', grain='all orders',
             duplicate_handling=f'Count {key} once; a table row is one size, not one order.'),
        dict(name='Quantity by size', definition='Pieces per size across orders.', formula=f'SUM(ORDERQTY) FROM {table} GROUP BY SIZES', grain='one size name',
             null_handling='Size names are compared exactly as written in Excel ("Up To 1M" and "Up To 1Mth" are different).'),
    ]:
        propose_metric(dict(m, source_tables=[table]), 'POPPYS (proposed)')


def context(q, relationships):
    """What a future question-answering agent may use for a question: AI-ready tables and columns, verified joins,
    approved metrics and the query policy. Nothing unverified is included."""
    hits = search(q, 30, ai_ready_only=True)
    names = sorted({h['table'] for h in hits if h.get('table')})
    return dict(question=q, tables=[card(n, relationships, metrics('APPROVED')) for n in names[:8]],
                metrics=[m for m in metrics('APPROVED')],
                policy=['Read-only SELECT statements only; never INSERT, UPDATE, DELETE or DDL.',
                        'Join only through relationships marked join_verified.',
                        'Respect each table grain; do not SUM a column that repeats on every row of a larger entity.',
                        'Use approved metric definitions when the question names a metric.',
                        'Tables that are not AI_READY are not used.'],
                note=None if names else 'No AI-ready table matches yet. Document tables and approve them in Database → Catalog.')
