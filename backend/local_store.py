"""Local workspace state (SQLite): users, sessions, drafts, revisions, audit, templates, mapping verification."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from .config import DATA

ROLES = ('admin', 'approver', 'operator', 'viewer')
ORDER_FIELDS = ('buyer', 'style', 'description', 'po_number', 'order_ref', 'ship_date', 'order_date', 'season', 'merch',
                'sample_type', 'pieces_per_unit', 'quantity_basis', 'exporter', 'against', 'department', 'gender',
                'packing_type', 'components')
FABRIC_FIELDS = ('component', 'portion', 'line', 'color', 'base_color', 'quality', 'gsm', 'content', 'count', 'dia',
                 'yy', 'rejection')
YARN_FIELDS = ('count', 'color', 'percent', 'description')
ACCESSORY_FIELDS = ('category', 'name', 'code', 'description', 'color', 'size', 'supplier', 'line', 'uom', 'total')


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


@contextmanager
def db():
    with sqlite3.connect(DATA / 'poppys.sqlite3', timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        yield conn


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,draft_id TEXT,version INTEGER,actor TEXT,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,actor TEXT,action TEXT NOT NULL,target TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,signature TEXT NOT NULL,labels TEXT NOT NULL,
            definition TEXT NOT NULL,created_by TEXT,created_at TEXT,uses INTEGER DEFAULT 0,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS mapping_overrides(table_name TEXT NOT NULL,column_name TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,
            actor TEXT,at TEXT,note TEXT,PRIMARY KEY(table_name,column_name));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        cols = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        # Additive migration: the original single account becomes the administrator.
        if 'role' not in cols:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        if 'active' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        if 'created_at' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN created_at TEXT')


# --- audit -----------------------------------------------------------------
def audit(actor, action, target=None, **detail):
    with db() as c:
        c.execute('INSERT INTO audit(at,actor,action,target,detail) VALUES (?,?,?,?,?)',
                  (now(), actor, action, target, json.dumps(detail, ensure_ascii=False, default=str)))


def audit_events(limit=200, target=None):
    with db() as c:
        sql = 'SELECT * FROM audit' + (' WHERE target=?' if target else '') + ' ORDER BY id DESC LIMIT ?'
        rows = c.execute(sql, ((target, limit) if target else (limit,))).fetchall()
        return [dict(id=r['id'], at=r['at'], actor=r['actor'], action=r['action'], target=r['target'],
                     detail=json.loads(r['detail'] or '{}')) for r in rows]


# --- settings & mapping verification ----------------------------------------
def get_setting(key, default=None):
    with db() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default


def set_setting(key, value):
    with db() as c:
        c.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


def mapping_overrides():
    try:
        with db() as c:
            return [dict(table=r['table_name'], column=r['column_name'], status=r['status'], actor=r['actor'], at=r['at'], note=r['note'])
                    for r in c.execute('SELECT * FROM mapping_overrides ORDER BY table_name,column_name')]
    except sqlite3.OperationalError:
        return []


def set_mapping_status(table, column, status, actor, note=''):
    with db() as c:
        c.execute('''INSERT INTO mapping_overrides VALUES (?,?,?,?,?,?) ON CONFLICT(table_name,column_name)
            DO UPDATE SET status=excluded.status,actor=excluded.actor,at=excluded.at,note=excluded.note''',
                  (table, column or '', status, actor, now(), note))


# --- templates ---------------------------------------------------------------
def templates(active_only=True):
    with db() as c:
        rows = c.execute('SELECT * FROM templates' + (' WHERE active=1' if active_only else '') + ' ORDER BY uses DESC,created_at DESC').fetchall()
        return [dict(id=r['id'], name=r['name'], signature=r['signature'], labels=json.loads(r['labels']),
                     definition=json.loads(r['definition']), created_by=r['created_by'], created_at=r['created_at'],
                     uses=r['uses'], active=bool(r['active'])) for r in rows]


def save_template(name, signature, labels, definition, actor):
    tid = uuid.uuid4().hex
    with db() as c:
        c.execute('INSERT INTO templates(id,name,signature,labels,definition,created_by,created_at) VALUES (?,?,?,?,?,?,?)',
                  (tid, name, signature, json.dumps(sorted(labels)), json.dumps(definition), actor, now()))
    return tid


def template_used(tid):
    with db() as c:
        c.execute('UPDATE templates SET uses=uses+1 WHERE id=?', (tid,))


def set_template_active(tid, active):
    with db() as c:
        if c.execute('UPDATE templates SET active=? WHERE id=?', (1 if active else 0, tid)).rowcount != 1:
            raise KeyError('Template not found')


# --- drafts --------------------------------------------------------------------
def identify(orders):
    for o in orders:
        o.setdefault('id', uuid.uuid4().hex)
        for key in ('lines', 'order_lines', 'sample_lines'):
            for line in o.get(key, []):
                line.setdefault('id', uuid.uuid4().hex)
                for s in line.get('sizes', []):
                    s.setdefault('id', uuid.uuid4().hex)
        for f in o.get('fabrics', []):
            f.setdefault('id', uuid.uuid4().hex)
            for y in f.get('yarns', []):
                y.setdefault('id', uuid.uuid4().hex)
        for a in o.get('accessories', []):
            a.setdefault('id', uuid.uuid4().hex)
    return orders


def get(id):
    with db() as c:
        row = c.execute('SELECT payload FROM drafts WHERE id=?', (id,)).fetchone()
        if not row:
            raise KeyError('Draft not found')
        return json.loads(row['payload'])


def save(draft, actor, expected=None):
    with db() as c:
        payload = json.dumps(draft, ensure_ascii=False)
        if expected is None:
            c.execute('INSERT INTO drafts VALUES (?,?,?)', (draft['id'], draft['version'], payload))
        else:
            cursor = c.execute('UPDATE drafts SET version=?,payload=? WHERE id=? AND version=?',
                               (draft['version'], payload, draft['id'], expected))
            if cursor.rowcount != 1:
                raise ValueError('Draft changed in another window. Reload before continuing.')
        c.execute('INSERT INTO revisions(draft_id,version,actor,payload) VALUES (?,?,?,?)', (draft['id'], draft['version'], actor, payload))


def summaries():
    with db() as c:
        return [dict(id=d['id'], version=d['version'], filename=d['filename'], format=d['format'], kind=d.get('kind', 'excel'),
                     created_at=d.get('created_at'), uploaded_by=d.get('uploaded_by'),
                     status='Saved to Oracle' if d.get('result') else 'Preview ready' if d.get('plan') else 'Draft',
                     committed=bool(d.get('result')), orders=len(d['orders']),
                     styles=[o.get('style') for o in d['orders']][:5])
                for r in c.execute('SELECT payload FROM drafts ORDER BY rowid DESC LIMIT 100') for d in [json.loads(r[0])]]


def _merge_rows(old_rows, incoming, fields, child=None):
    """Keep server-owned provenance for known rows; accept only whitelisted business fields."""
    by_id = {r['id']: r for r in old_rows if 'id' in r}; out = []
    for row in incoming or []:
        prev = by_id.get(row.get('id'))
        clean = deepcopy(prev) if prev else dict(id=uuid.uuid4().hex, source={'origin': 'Manual entry'})
        for f in fields:
            if f in row:
                clean[f] = row[f]
        if child:
            key, child_fields = child
            clean[key] = _merge_rows((prev or {}).get(key, []), row.get(key, []), child_fields)
        out.append(clean)
    return out


def edited_orders(old_orders, incoming):
    # Keep original workbook provenance and snapshots server-owned, even after user corrections.
    result = []; old_by_id = {o['id']: o for o in old_orders}
    for o in incoming:
        original = old_by_id.get(o.get('id'))
        if not original:
            if o.get('id'):
                raise ValueError('Unknown order identity. Reload this draft.')
            from .workbooks import base_order
            original = base_order()
            original['id'] = uuid.uuid4().hex
            original['warnings'] = ['Manually entered order. Review every value before accepting.']
        new = deepcopy(original)
        for field in ORDER_FIELDS:
            if field in o:
                new[field] = o[field]
        existing_lines = {l['id']: l for key in ('lines', 'order_lines', 'sample_lines') for l in original.get(key, [])}
        new['lines'] = []
        for line in o.get('lines', []):
            source_line = existing_lines.get(line.get('id'), {})
            clean = deepcopy(source_line) or dict(id=uuid.uuid4().hex, provenance={})
            for f in ('label', 'color'):
                clean[f] = str(line.get(f, ''))
            clean['sizes'] = []
            originals = {s['id']: s for s in source_line.get('sizes', [])}
            changed = False
            for s in line.get('sizes', []):
                prev = originals.get(s.get('id'), {})
                row = deepcopy(prev) or dict(id=uuid.uuid4().hex, source={'origin': 'Manual correction'})
                row['size'] = str(s.get('size', '')); row['quantity'] = s.get('quantity')
                if row['size'] != prev.get('size') or str(row['quantity']) != str(prev.get('quantity')):
                    changed = True
                clean['sizes'].append(row)
            changed = changed or len(clean['sizes']) != len(source_line.get('sizes', []))
            # Only an actual quantity/size edit can waive the original workbook total comparison.
            clean['declared_total'] = None if changed else source_line.get('declared_total')
            clean['original_total'] = source_line.get('original_total', source_line.get('declared_total'))
            new['lines'].append(clean)
        if 'fabrics' in o:
            new['fabrics'] = _merge_rows(original.get('fabrics', []), o['fabrics'], FABRIC_FIELDS, ('yarns', YARN_FIELDS))
        if 'accessories' in o:
            new['accessories'] = _merge_rows(original.get('accessories', []), o['accessories'], ACCESSORY_FIELDS)
        result.append(new)
    return result


def changes(old_orders, new_orders):
    """Field-level correction log for the audit trail."""
    out = []
    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a) | set(b):
                if k in ('source', 'provenance', 'size_source', 'id', 'warnings', 'order_lines', 'sample_lines'):
                    continue
                walk(a.get(k), b.get(k), f'{path}.{k}' if path else k)
        elif isinstance(a, list) and isinstance(b, list) and all(isinstance(x, dict) for x in a + b):
            ida = {x.get('id'): x for x in a}; idb = {x.get('id'): x for x in b}
            for i, x in enumerate(b):
                walk(ida.get(x.get('id'), {}), x, f'{path}[{i}]')
            for x in a:
                if x.get('id') not in idb:
                    out.append(dict(field=path, before='(removed row)', after=None))
        elif a != b:
            out.append(dict(field=path, before=a, after=b))
    walk({'orders': old_orders}, {'orders': new_orders}, '')
    return out[:200]
