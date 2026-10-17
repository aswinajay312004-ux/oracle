"""Local workspace state (SQLite): users, sessions, drafts, revisions, audit, templates, mapping verification.

Durability (docs/RESEARCH_DECISIONS.md, "Logging and ops: SQLite durability, backups, audit, versioning"):
* Every connection is closed when the block ends (``with sqlite3.connect()`` only commits or rolls back; it
  never closes, which leaks a file handle on Windows). One block = one transaction.
* journal_mode stays DELETE: WAL has a reset bug in SQLite 3.7.0-3.51.2 and CPython 3.13 on Windows bundles
  3.50.4. synchronous=FULL, foreign_keys=ON and a 20 s busy timeout on every connection.
* Schema changes are numbered migrations recorded in PRAGMA user_version. Before an existing database is
  migrated, a copy is made with the SQLite backup API and checked (integrity_check, same tables and row
  counts); if that copy cannot be made and verified, nothing is changed and the app refuses to start.
* The audit table is append-only (BEFORE UPDATE / DELETE triggers abort) and hash-chained: each row stores the
  previous row's hash and a SHA-256 over itself, so an edit, a deleted row or a removed trigger is found by
  verify_audit_chain().
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import time
import uuid
from .config import DATA

DB_NAME = 'poppys.sqlite3'
BUSY_TIMEOUT_S = 20
GENESIS = '0' * 64

ROLES = ('admin', 'approver', 'operator', 'viewer')
ORDER_FIELDS = ('buyer', 'style', 'description', 'po_number', 'order_ref', 'ship_date', 'order_date', 'season', 'merch',
                'sample_type', 'pieces_per_unit', 'quantity_basis', 'exporter', 'against', 'department', 'gender',
                'packing_type', 'components', 'qty_unit')
FABRIC_FIELDS = ('component', 'portion', 'line', 'color', 'base_color', 'quality', 'gsm', 'content', 'count', 'dia',
                 'yy', 'rejection')
YARN_FIELDS = ('count', 'color', 'percent', 'description')
ACCESSORY_FIELDS = ('category', 'name', 'code', 'description', 'color', 'size', 'supplier', 'line', 'uom', 'total')


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def path():
    return DATA / DB_NAME


def _connect(file=None, timeout=BUSY_TIMEOUT_S):
    conn = sqlite3.connect(file or path(), timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute(f'PRAGMA busy_timeout={int(timeout * 1000)}')
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def db():
    """One connection and one transaction: committed when the block ends normally, rolled back on an
    exception, and closed either way."""
    with closing(_connect()) as conn:
        with conn:
            yield conn


# --- schema and migrations -----------------------------------------------------------
def _columns(c, table):
    return {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}


def _m1_baseline(c):
    """The schema as it was before numbered migrations (every step is idempotent, so it also upgrades any
    older copy that had only some of these tables or columns)."""
    for ddl in (
        'CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL)',
        'CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,draft_id TEXT,version INTEGER,actor TEXT,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
        'CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,actor TEXT,action TEXT NOT NULL,target TEXT,detail TEXT)',
        '''CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,signature TEXT NOT NULL,labels TEXT NOT NULL,
            definition TEXT NOT NULL,created_by TEXT,created_at TEXT,uses INTEGER DEFAULT 0,active INTEGER DEFAULT 1)''',
        '''CREATE TABLE IF NOT EXISTS mapping_overrides(table_name TEXT NOT NULL,column_name TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,
            actor TEXT,at TEXT,note TEXT,PRIMARY KEY(table_name,column_name))''',
        'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS knowledge_overrides(object TEXT PRIMARY KEY,data TEXT NOT NULL,actor TEXT,at TEXT)',
        'CREATE TABLE IF NOT EXISTS value_index(tbl TEXT NOT NULL,col TEXT NOT NULL,value TEXT NOT NULL,norm TEXT NOT NULL,n INTEGER)',
        'CREATE INDEX IF NOT EXISTS value_index_norm ON value_index(norm)',
        '''CREATE TABLE IF NOT EXISTS nl_examples(id INTEGER PRIMARY KEY,masked TEXT NOT NULL,question TEXT NOT NULL,intent TEXT NOT NULL,
            actor TEXT,at TEXT,uses INTEGER DEFAULT 0)''',
        'CREATE TABLE IF NOT EXISTS chat_log(id TEXT PRIMARY KEY,username TEXT,at TEXT,question TEXT,payload TEXT,feedback TEXT)',
        'CREATE TABLE IF NOT EXISTS field_settings(key TEXT PRIMARY KEY,required INTEGER,aliases TEXT,actor TEXT,at TEXT)',
    ):
        c.execute(ddl)
    tcols = _columns(c, 'templates')
    # Versioned buyer templates (docs/EXCEL_UNDERSTANDING.md §6).
    for col, ddl in (('buyer', "TEXT NOT NULL DEFAULT ''"), ('version', 'INTEGER NOT NULL DEFAULT 1'), ('family', 'TEXT'),
                     ('anchors', 'TEXT'), ('unit', 'TEXT'), ('quantity_basis', 'TEXT')):
        if col not in tcols:
            c.execute(f'ALTER TABLE templates ADD COLUMN {col} {ddl}')
    c.execute('UPDATE templates SET family=id WHERE family IS NULL')
    cols = _columns(c, 'users')
    # The original single account becomes the administrator.
    if 'role' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    if 'active' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
    if 'created_at' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN created_at TEXT')


def _m2_sessions_and_sign_in(c):
    """Sessions with idle and absolute expiry; sign-in failures and username locks kept across restarts; a
    one-row table that /readyz writes to prove the database accepts writes. Sessions from before have no
    last-activity time, so they end: everyone signs in once after this upgrade."""
    c.execute('DROP TABLE IF EXISTS sessions')
    c.execute('''CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,created REAL NOT NULL,
                 last_seen REAL NOT NULL,expires REAL NOT NULL)''')
    c.execute('CREATE INDEX sessions_user ON sessions(username)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_failures(id INTEGER PRIMARY KEY,username TEXT NOT NULL,ip TEXT NOT NULL,at REAL NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS auth_failures_user ON auth_failures(username,at)')
    c.execute('CREATE INDEX IF NOT EXISTS auth_failures_ip ON auth_failures(ip,at)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_locks(username TEXT PRIMARY KEY,until REAL NOT NULL,at TEXT NOT NULL,failures INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS health_probe(id INTEGER PRIMARY KEY CHECK (id=1),at REAL NOT NULL)')


def _m3_audit_chain(c):
    """Hash-chain the existing audit rows in id order, then make the table append-only."""
    cols = _columns(c, 'audit')
    if 'prev_hash' not in cols:
        c.execute('ALTER TABLE audit ADD COLUMN prev_hash TEXT')
    if 'hash' not in cols:
        c.execute('ALTER TABLE audit ADD COLUMN hash TEXT')
    prev = GENESIS
    for r in c.execute('SELECT id,at,actor,action,target,detail FROM audit ORDER BY id').fetchall():
        h = _audit_hash(prev, r)
        c.execute('UPDATE audit SET prev_hash=?,hash=? WHERE id=?', (prev, h, r['id']))
        prev = h
    _m3_audit_chain_triggers(c)


MIGRATIONS = ((1, _m1_baseline), (2, _m2_sessions_and_sign_in), (3, _m3_audit_chain))
SCHEMA_VERSION = MIGRATIONS[-1][0]


def schema_version(file=None):
    with closing(_connect(file)) as c:
        return c.execute('PRAGMA user_version').fetchone()[0]


def _snapshot(c):
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


def backup_before_migration(from_version):
    """Copy the live database with the SQLite backup API into DATA/backups/pre-migration/ and verify the copy
    (integrity_check ok, same user_version, same tables with the same row counts). Returns the copy's path;
    raises RuntimeError (nothing changed) when the copy is not good."""
    folder = DATA / 'backups' / 'pre-migration'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dest = folder / f'poppys-v{from_version}-before-v{SCHEMA_VERSION}-{stamp}-{uuid.uuid4().hex[:6]}.sqlite3'
    partial = dest.with_name(dest.name + '.partial')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with closing(_connect()) as src, closing(sqlite3.connect(partial)) as out:
            src.execute('BEGIN')                      # one consistent snapshot for the copy and the counts
            expected = _snapshot(src)
            src.backup(out, pages=1024, sleep=0.05)
            src.rollback()
        with closing(sqlite3.connect(partial.resolve().as_uri() + '?mode=ro', uri=True)) as check:
            ok = check.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            same = check.execute('PRAGMA user_version').fetchone()[0] == from_version and \
                {t: check.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in expected} == expected
        if not (ok and same):
            raise RuntimeError('the copy did not match the database')
        os.replace(partial, dest)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f'The local database needs an upgrade, but a verified backup could not be made first ({exc}). '
                           f'Nothing was changed. Free some disk space or check the data folder, then start POPPYS AI again.') from exc
    return dest


def init():
    """Create or upgrade the local database to SCHEMA_VERSION. Returns the pre-migration backup path, if any."""
    with closing(_connect()) as c:
        c.execute('PRAGMA journal_mode=DELETE')
        current = c.execute('PRAGMA user_version').fetchone()[0]
        has_tables = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if current > SCHEMA_VERSION:
        raise RuntimeError(f'This database was upgraded by a newer POPPYS AI (schema {current}; this version knows {SCHEMA_VERSION}). '
                           'Start the newer version, or restore the backup made before that upgrade.')
    if current == SCHEMA_VERSION:
        return None
    copy = backup_before_migration(current) if has_tables else None
    for version, step in MIGRATIONS:
        if version <= current:
            continue
        with closing(_connect()) as c:
            c.isolation_level = None                  # explicit transaction: DDL and data change together or not at all
            c.execute('BEGIN IMMEDIATE')
            try:
                if c.execute('PRAGMA user_version').fetchone()[0] < version:   # not already done by a concurrent start
                    step(c)
                    c.execute(f'PRAGMA user_version={version}')
                c.execute('COMMIT')
            except BaseException:
                c.execute('ROLLBACK')
                raise
    return copy


def probe(timeout=2.0):
    """For /readyz: the database opens, has the expected schema and accepts a write. Raises on any problem."""
    with closing(_connect(timeout=timeout)) as c:
        version = c.execute('PRAGMA user_version').fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RuntimeError(f'schema {version}, expected {SCHEMA_VERSION}')
        with c:
            c.execute('INSERT INTO health_probe(id,at) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET at=excluded.at', (time.time(),))
    return True


# --- audit (append-only, hash-chained) ---------------------------------------
def _audit_canonical(r):
    return json.dumps([r['id'], r['at'], r['actor'], r['action'], r['target'], r['detail']],
                      ensure_ascii=False, separators=(',', ':'))


def _audit_hash(prev, r):
    return hashlib.sha256((prev + '\n' + _audit_canonical(r)).encode('utf-8')).hexdigest()


def audit(actor, action, target=None, **detail):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')      # one writer at a time, so two events never chain to the same row
        last = c.execute('SELECT id,hash FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        row = dict(id=(last['id'] if last else 0) + 1, at=now(), actor=actor, action=action, target=target,
                   detail=json.dumps(detail, ensure_ascii=False, default=str))
        prev = (last['hash'] if last else None) or GENESIS
        c.execute('INSERT INTO audit(id,at,actor,action,target,detail,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?)',
                  (row['id'], row['at'], actor, action, target, row['detail'], prev, _audit_hash(prev, row)))


def verify_audit_chain():
    """{ok, rows, head, problem?, row?}: every row links to the one before and still has its own hash, and the
    append-only triggers are in place."""
    with closing(_connect()) as c:
        triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit'")}
        missing = {'audit_no_update', 'audit_no_delete'} - triggers
        prev = GENESIS; n = 0; last_id = 0
        for r in c.execute('SELECT * FROM audit ORDER BY id'):
            if r['prev_hash'] != prev:
                return dict(ok=False, rows=n, row=r['id'], problem='This entry does not follow the one before it: an entry was removed or inserted.')
            if _audit_hash(prev, r) != r['hash']:
                return dict(ok=False, rows=n, row=r['id'], problem='This entry was changed after it was written.')
            prev = r['hash']; n += 1; last_id = r['id']
    if missing:
        return dict(ok=False, rows=n, row=None, problem='The protection that keeps the audit history append-only was removed.')
    return dict(ok=True, rows=n, head=prev, last_id=last_id)


def audit_tamper_for_tests(sql, params=()):
    """Only for tests: run one statement with the append-only triggers dropped (and put them back), to show
    that verify_audit_chain() notices changes made behind the application's back."""
    with closing(_connect()) as c:
        c.isolation_level = None
        c.execute('BEGIN IMMEDIATE')
        c.execute('DROP TRIGGER audit_no_update'); c.execute('DROP TRIGGER audit_no_delete')
        c.execute(sql, params)
        _m3_audit_chain_triggers(c)
        c.execute('COMMIT')


def _m3_audit_chain_triggers(c):
    c.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END")
    c.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END")


# --- sessions ------------------------------------------------------------------
def session_create(token_hash, username, at, absolute, idle, drop=None):
    """Store a new session; also removes the session the browser presented (drop) and every expired one."""
    with db() as c:
        if drop:
            c.execute('DELETE FROM sessions WHERE token_hash=?', (drop,))
        c.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<=?', (at, at - idle))
        c.execute('INSERT INTO sessions(token_hash,username,created,last_seen,expires) VALUES (?,?,?,?,?)',
                  (token_hash, username, at, at, at + absolute))


def session_user(token_hash, at, idle, touch_after=None):
    """(username, role) for a live session of an active user, else None. An expired session is deleted.
    touch_after: refresh last_seen when it is older than this many seconds (None: never)."""
    with db() as c:
        row = c.execute('''SELECT s.username,s.last_seen,s.expires,u.role FROM sessions s JOIN users u ON u.username=s.username
                           WHERE s.token_hash=? AND u.active=1''', (token_hash,)).fetchone()
        if not row:
            return None
        if row['expires'] <= at or row['last_seen'] + idle <= at:
            c.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))
            return None
        if touch_after is not None and at - row['last_seen'] >= touch_after:
            c.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (at, token_hash))
        return row['username'], row['role']


def session_delete(token_hash):
    """Returns the username whose session it was (or None)."""
    with db() as c:
        row = c.execute('SELECT username FROM sessions WHERE token_hash=?', (token_hash,)).fetchone()
        c.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))
        return row['username'] if row else None


def sessions_end_for(username):
    with db() as c:
        return c.execute('DELETE FROM sessions WHERE username=?', (username,)).rowcount


def sessions_end_all(keep=None):
    with db() as c:
        return c.execute('DELETE FROM sessions WHERE token_hash<>?', (keep or '',)).rowcount


# --- sign-in failures and locks --------------------------------------------------------
def login_reserve(username, ip, at, window, wait_for):
    """In one write transaction: refuse the attempt -> (seconds, 'locked' | 'throttled', None), or record it as
    a failure in advance -> (0, None, row id). Recording first means attempts made at the same moment cannot
    all slip past the limit; a successful sign-in removes the row again (login_succeeded)."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM auth_failures WHERE at<?', (at - 86400,))
        lock = c.execute('SELECT until FROM auth_locks WHERE username=?', (username,)).fetchone()
        if lock and lock['until'] > at:
            return max(1, int(lock['until'] - at + 0.999)), 'locked', None
        if lock:
            c.execute('DELETE FROM auth_locks WHERE username=?', (username,))
        u = c.execute('SELECT count(*),max(at) FROM auth_failures WHERE username=? AND at>?', (username, at - window)).fetchone()
        i = c.execute('SELECT count(*),max(at) FROM auth_failures WHERE ip=? AND at>?', (ip, at - window)).fetchone()
        wait = max(wait_for(u[0], u[1], at), wait_for(i[0], i[1], at))
        if wait > 0:
            return wait, 'throttled', None
        cur = c.execute('INSERT INTO auth_failures(username,ip,at) VALUES (?,?,?)', (username, ip, at))
        return 0, None, cur.lastrowid


def login_failed(username, at, window, lock_after, lock_seconds):
    """The reserved failure stays. Returns the lock end time when this failure locked the username, else None."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        n = c.execute('SELECT count(*) FROM auth_failures WHERE username=? AND at>?', (username, at - window)).fetchone()[0]
        if n >= lock_after:
            until = at + lock_seconds
            c.execute('INSERT INTO auth_locks(username,until,at,failures) VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET '
                      'until=excluded.until,at=excluded.at,failures=excluded.failures', (username, until, now(), n))
            return until
    return None


def login_succeeded(username, reserved_id):
    with db() as c:
        c.execute('DELETE FROM auth_failures WHERE id=? OR username=?', (reserved_id, username))


def unlock(username):
    """Returns True when the username was locked."""
    with db() as c:
        was = c.execute('DELETE FROM auth_locks WHERE username=? AND until>?', (username, time.time())).rowcount
        c.execute('DELETE FROM auth_locks WHERE username=?', (username,))
        c.execute('DELETE FROM auth_failures WHERE username=?', (username,))
        return bool(was)


def locks(at=None):
    """{username (lower case): lock end time} for locks still in force."""
    with db() as c:
        return {r['username']: r['until'] for r in c.execute('SELECT username,until FROM auth_locks WHERE until>?', (at or time.time(),))}


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
def _template(r):
    return dict(id=r['id'], name=r['name'], buyer=r['buyer'] or '', version=r['version'] or 1, family=r['family'] or r['id'],
                signature=r['signature'], labels=json.loads(r['labels']), definition=json.loads(r['definition']),
                anchors=json.loads(r['anchors']) if r['anchors'] else None, unit=json.loads(r['unit']) if r['unit'] else None,
                quantity_basis=r['quantity_basis'], created_by=r['created_by'], created_at=r['created_at'],
                uses=r['uses'], active=bool(r['active']))


def templates(active_only=True):
    """Active templates for parsing; with active_only=False the whole version history, newest version first per layout."""
    with db() as c:
        order = ' ORDER BY uses DESC,created_at DESC' if active_only else ' ORDER BY lower(name),family,version DESC'
        rows = c.execute('SELECT * FROM templates' + (' WHERE active=1' if active_only else '') + order).fetchall()
        return [_template(r) for r in rows]


def save_template(name, signature, labels, definition, actor, buyer='', anchors=None, unit=None, quantity_basis=None):
    """Save a confirmed layout. The same name, or the same signature for the same buyer, is a new version of
    that layout: version n+1 becomes the active one and older versions are kept, inactive, as history."""
    tid = uuid.uuid4().hex; buyer = ' '.join(str(buyer or '').split())
    with db() as c:
        prev = c.execute("SELECT family, max(version) AS v FROM templates WHERE lower(name)=lower(?) "
                         "OR (signature=? AND buyer<>'' AND upper(buyer)=upper(?)) GROUP BY family ORDER BY v DESC LIMIT 1",
                         (name, signature, buyer)).fetchone()
        family = prev['family'] if prev else tid
        version = (prev['v'] or 0) + 1 if prev else 1
        c.execute('UPDATE templates SET active=0 WHERE family=?', (family,))
        c.execute('INSERT INTO templates(id,name,signature,labels,definition,created_by,created_at,buyer,version,family,anchors,unit,quantity_basis) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (tid, name, signature, json.dumps(sorted(labels)), json.dumps(definition), actor, now(), buyer, version, family,
                   None if anchors is None else json.dumps(anchors, ensure_ascii=False),
                   None if unit is None else json.dumps(unit), quantity_basis))
    return dict(id=tid, version=version, family=family)


def template_used(tid):
    with db() as c:
        c.execute('UPDATE templates SET uses=uses+1 WHERE id=?', (tid,))


def fill_template_unit(tid, unit=None, basis=None):
    """Remember a unit / quantity basis the template does not know yet; a saved decision is never overwritten.
    Returns what was learned ({unit?, quantity_basis?}) or {}."""
    learned = {}
    with db() as c:
        row = c.execute('SELECT unit,quantity_basis FROM templates WHERE id=?', (tid,)).fetchone()
        if not row:
            return learned
        known = json.loads(row['unit']) if row['unit'] else {}
        if unit and not known.get('qty_unit'):
            c.execute('UPDATE templates SET unit=? WHERE id=?', (json.dumps(unit), tid)); learned['unit'] = unit
        if basis and not row['quantity_basis']:
            c.execute('UPDATE templates SET quantity_basis=? WHERE id=?', (basis, tid)); learned['quantity_basis'] = basis
    return learned


def set_template_active(tid, active):
    """Activating one version deactivates the other versions of the same layout (one active version each)."""
    with db() as c:
        row = c.execute('SELECT family FROM templates WHERE id=?', (tid,)).fetchone()
        if not row:
            raise KeyError('Template not found')
        if active:
            c.execute('UPDATE templates SET active=0 WHERE family=? AND id<>?', (row['family'] or tid, tid))
        c.execute('UPDATE templates SET active=? WHERE id=?', (1 if active else 0, tid))


# --- order fields (aliases / required) -------------------------------------------
def field_settings():
    with db() as c:
        return {r['key']: dict(required=None if r['required'] is None else bool(r['required']),
                               aliases=None if r['aliases'] is None else json.loads(r['aliases']), actor=r['actor'], at=r['at'])
                for r in c.execute('SELECT * FROM field_settings')}


def set_field_setting(key, actor, required=None, aliases=None):
    """Save one field's changes; None keeps that part as it is."""
    with db() as c:
        old = c.execute('SELECT * FROM field_settings WHERE key=?', (key,)).fetchone()
        req = (1 if required else 0) if required is not None else (old['required'] if old else None)
        als = json.dumps(aliases, ensure_ascii=False) if aliases is not None else (old['aliases'] if old else None)
        c.execute('INSERT INTO field_settings VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET required=excluded.required,'
                  'aliases=excluded.aliases,actor=excluded.actor,at=excluded.at', (key, req, als, actor, now()))


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


def _pieces(orders):
    total = 0
    for o in orders:
        try:
            per = int(o.get('pieces_per_unit') or 1)
            total += per * sum(int(float(s.get('quantity') or 0)) for line in o.get('lines', []) for s in line.get('sizes', []))
        except (TypeError, ValueError):
            continue
    return total


def _status(d):
    p = d.get('plan')
    if d.get('result'):
        return 'Saved to Oracle'
    if not p:
        return 'Draft'
    if p.get('errors'):
        return 'Needs attention'
    return 'Ready to approve' if p.get('live') else 'Checked offline'


def summaries():
    with db() as c:
        return [dict(id=d['id'], version=d['version'], filename=d['filename'], format=d['format'], kind=d.get('kind', 'excel'),
                     created_at=d.get('created_at'), uploaded_by=d.get('uploaded_by'),
                     status=_status(d),
                     committed=bool(d.get('result')), orders=len(d['orders']),
                     styles=[o.get('style') for o in d['orders']][:5], buyers=[o.get('buyer') for o in d['orders']][:5],
                     order_refs=[o.get('order_ref') for o in d['orders']][:5], pieces=_pieces(d['orders']))
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


# --- data intelligence: metadata edits, value index, saved examples, chat log --------------------
def knowledge_overrides():
    with db() as c:
        return {r['object']: json.loads(r['data']) for r in c.execute('SELECT * FROM knowledge_overrides')}


def set_knowledge_override(obj, data, actor):
    with db() as c:
        old = c.execute('SELECT data FROM knowledge_overrides WHERE object=?', (obj,)).fetchone()
        merged = {**(json.loads(old['data']) if old else {}), **data}
        c.execute('INSERT INTO knowledge_overrides VALUES (?,?,?,?) ON CONFLICT(object) DO UPDATE SET data=excluded.data,actor=excluded.actor,at=excluded.at',
                  (obj, json.dumps(merged, ensure_ascii=False), actor, now()))
        return merged


def replace_values(rows, tables=None):
    """rows: iterable of (table, column, value, norm, count). Replaces the index for the given tables."""
    with db() as c:
        if tables is None:
            c.execute('DELETE FROM value_index')
        else:
            c.executemany('DELETE FROM value_index WHERE tbl=?', [(t,) for t in tables])
        c.executemany('INSERT INTO value_index VALUES (?,?,?,?,?)', rows)


def find_values(normalized, limit=12):
    with db() as c:
        return [dict(table=r['tbl'], column=r['col'], value=r['value'], n=r['n'])
                for r in c.execute('SELECT * FROM value_index WHERE norm=? ORDER BY n DESC LIMIT ?', (normalized, limit))]


def value_stats():
    with db() as c:
        return [dict(table=r[0], column=r[1], values=r[2]) for r in c.execute('SELECT tbl,col,count(*) FROM value_index GROUP BY tbl,col ORDER BY tbl,col')]


def examples():
    with db() as c:
        return [dict(id=r['id'], masked=r['masked'], question=r['question'], intent=json.loads(r['intent']), actor=r['actor'], at=r['at'], uses=r['uses'])
                for r in c.execute('SELECT * FROM nl_examples ORDER BY id DESC')]


def save_example(masked, question, intent, actor):
    with db() as c:
        c.execute('DELETE FROM nl_examples WHERE masked=?', (masked,))
        cur = c.execute('INSERT INTO nl_examples(masked,question,intent,actor,at) VALUES (?,?,?,?,?)',
                        (masked, question, json.dumps(intent, ensure_ascii=False), actor, now()))
        return cur.lastrowid


def delete_example(eid):
    with db() as c:
        c.execute('DELETE FROM nl_examples WHERE id=?', (eid,))


def example_used(eid):
    with db() as c:
        c.execute('UPDATE nl_examples SET uses=uses+1 WHERE id=?', (eid,))


def log_chat(cid, username, question, payload):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO chat_log(id,username,at,question,payload) VALUES (?,?,?,?,?)',
                  (cid, username, now(), question, json.dumps(payload, ensure_ascii=False, default=str)))


def chat_entry(cid):
    with db() as c:
        r = c.execute('SELECT * FROM chat_log WHERE id=?', (cid,)).fetchone()
        if not r:
            raise KeyError(cid)
        return dict(id=r['id'], username=r['username'], at=r['at'], question=r['question'], payload=json.loads(r['payload']), feedback=r['feedback'])


def chat_feedback(cid, value):
    with db() as c:
        c.execute('UPDATE chat_log SET feedback=? WHERE id=?', (value, cid))


def chat_history(username, limit=30):
    with db() as c:
        return [dict(id=r['id'], at=r['at'], question=r['question'], feedback=r['feedback'])
                for r in c.execute('SELECT id,at,question,feedback FROM chat_log WHERE username=? ORDER BY rowid DESC LIMIT ?', (username, limit))]
