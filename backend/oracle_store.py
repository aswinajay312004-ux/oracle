"""Restricted, bind-only GARMENT_DEMO writer. Table/column identifiers come only from the
committed mapping registry and are re-checked against the live data dictionary.

Connections come from small python-oracledb pools (docs/RESEARCH_DECISIONS.md, "Oracle access: driver and pools"):
one for the main account (POPPYS_APP) and, when .env.poppys names one, one for the read-only account POPPYS_RO
(scripts/create_readonly_user.py). connect() keeps its contract, `with connect() as conn:`, and closing the
connection hands its session back to the pool."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
import hashlib
import ipaddress
import json
import logging
import re
import sys
import threading
import oracledb
from .config import OWNER, setting
from .erp import planner, mapping as registry, readback
from .erp.catalog import OracleCatalog

digest = planner.digest
normalize = planner.normalize
count = planner.count
log = logging.getLogger('poppys.oracle')

# --- connection pools ---------------------------------------------------------------------------------
DEFAULT_DSN = '127.0.0.1:1521/FREEPDB1'
MODULE = 'POPPYS AI'        # SYS_CONTEXT('USERENV','MODULE') / V$SESSION.MODULE of every POPPYS request
PROGRAM = 'POPPYS-AI'       # V$SESSION.PROGRAM (the driver allows no spaces here)
CALL_TIMEOUT_MS = 30_000    # per round trip unless the caller sets its own (Ask AI/explorer 20 s, dictionary 60 s)
POOL = dict(min=1, max=4, increment=1,
            wait_timeout=3000,                 # ms an acquire waits while every session is busy; then RuntimeError → 503
            ping_interval=60, ping_timeout=3000,   # a session idle in the pool for 60 s is pinged (3 s limit) before reuse
            timeout=300,                       # s an idle session above `min` stays open
            max_lifetime_session=28800,        # s; every session is renewed at least each 8 hours
            tcp_connect_timeout=4.0, expire_time=2, stmtcachesize=40)
BROKEN = ('DPY-4011', 'DPY-4024')   # a session that hit one of these is closed, never handed back to the pool
MIN_VERSION = (12, 1)               # FETCH FIRST, READ grants (12.1.0.2), python-oracledb Thin mode
_CODE = re.compile(r'^((?:DPY|ORA)-\d{4,5})')
_pools = {}; _pools_lock = threading.Lock(); _noted = set()


def _code(exc):
    """'DPY-4024', 'ORA-01031'… of an oracledb error, '' for anything else."""
    arg = exc.args[0] if getattr(exc, 'args', None) else None
    code = getattr(arg, 'full_code', '') or ''
    if not code:
        found = _CODE.match(str(arg if isinstance(arg, str) else exc))
        code = found.group(1) if found else ''
    return code


def _broken(exc):
    """The reason a session must not be reused, found in exc or the errors it was raised from (TimedOut is raised
    from a DPY-4024 with `from None`, which still keeps it as __context__), else None."""
    for _ in range(6):
        if exc is None:
            return None
        arg = exc.args[0] if getattr(exc, 'args', None) else None
        if _code(exc) in BROKEN or getattr(arg, 'is_session_dead', False):
            return _code(exc) or 'session lost'
        exc = exc.__cause__ or exc.__context__
    return None


def _remember(conn, exc):
    reason = _broken(exc)
    if reason and not getattr(conn, '_poppys_broken', None):
        conn._poppys_broken = reason


class _WatchedCursor(oracledb.Cursor):
    """A cursor that tells its connection when a call broke the session, also when the caller then handles the
    error itself (the live finder treats a timed-out probe as 'not found' and goes on)."""

    def execute(self, *args, **kwargs):
        try:
            return super().execute(*args, **kwargs)
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def executemany(self, *args, **kwargs):
        try:
            return super().executemany(*args, **kwargs)
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def callproc(self, *args, **kwargs):
        try:
            return super().callproc(*args, **kwargs)
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def callfunc(self, *args, **kwargs):
        try:
            return super().callfunc(*args, **kwargs)
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def fetchone(self):
        try:
            return super().fetchone()
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def fetchmany(self, *args, **kwargs):
        try:
            return super().fetchmany(*args, **kwargs)
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def fetchall(self):
        try:
            return super().fetchall()
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise

    def __next__(self):
        try:
            return super().__next__()
        except oracledb.Error as exc:
            _remember(self.connection, exc); raise


class PooledConnection(oracledb.Connection):
    """What connect() returns (the pool's connectiontype). Closing it, or leaving `with`, hands the session back
    to its pool (rolled back), except a session that hit DPY-4011/DPY-4024 or is no longer healthy: that one is
    dropped, and the pool opens a fresh session when one is needed. Relies on python-oracledb 4.0.2 internals
    (`_impl`, `_pool`), pinned in requirements.txt and covered by tests/test_oracle_pool.py."""
    _poppys_broken = None

    def cursor(self, scrollable=False, handle=None):
        self._verify_connected()
        return _WatchedCursor(self, scrollable, handle=handle)

    def commit(self):
        try:
            return super().commit()
        except oracledb.Error as exc:
            _remember(self, exc); raise

    def rollback(self):
        try:
            return super().rollback()
        except oracledb.Error as exc:
            _remember(self, exc); raise

    def ping(self):
        try:
            return super().ping()
        except oracledb.Error as exc:
            _remember(self, exc); raise

    def __exit__(self, exc_type, exc_value, exc_tb):
        if exc_value is not None:
            _remember(self, exc_value)
        if self._impl is not None:
            self._release()

    def close(self):
        if self._impl is None:
            return super().close()      # DPY-1001, as for any closed connection
        self._release()

    def _release(self):
        pool = self._pool
        reason = self._poppys_broken or (None if self.is_healthy() else 'unhealthy')
        if pool is not None and reason:
            try:
                pool.drop(self)
                log.warning('Closed an Oracle session after %s instead of returning it to the pool.', reason)
                return
            except Exception:     # the pool may already have discarded it; a plain release is safe either way
                if self._impl is None:
                    return
        super().close()


def readonly_configured():
    return bool(setting('ORACLE_RO_USER') and setting('ORACLE_RO_PASSWORD'))


def _account(readonly):
    """('ro'|'main', user, password) for this request."""
    if readonly and readonly_configured():
        return 'ro', setting('ORACLE_RO_USER'), setting('ORACLE_RO_PASSWORD')
    if not setting('ORACLE_PASSWORD'):
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    if readonly and 'ro' not in _noted:
        _noted.add('ro')
        log.info('No read-only Oracle account is configured (ORACLE_RO_USER in .env.poppys), so read-only pages use %s. '
                 'Run scripts/create_readonly_user.py to add one.', setting('ORACLE_USER') or 'the main account')
    return 'main', setting('ORACLE_USER'), setting('ORACLE_PASSWORD')


def pool(readonly=False):
    """The (lazily created) pool for this account. A change of account, password or DSN gets a pool of its own."""
    kind, user, password = _account(readonly)
    dsn = setting('ORACLE_DSN', DEFAULT_DSN)
    key = (kind, user, dsn, hashlib.sha256(password.encode()).hexdigest())
    with _pools_lock:
        found = _pools.get(key)
        if found is None:
            found = _pools[key] = oracledb.create_pool(user=user, password=password, dsn=dsn, connectiontype=PooledConnection,
                                                       getmode=oracledb.POOL_GETMODE_TIMEDWAIT, program=PROGRAM, **POOL)
        return found


def _text(value, limit):
    """At most `limit` bytes, or None. Never '': python-oracledb 4.0.2 Thin loses the session (DPY-4011, seen on
    23ai) when MODULE, ACTION or CLIENT_IDENTIFIER is set to an empty string; None clears them safely."""
    value = str(value or '').strip().encode('utf-8')[:limit].decode('utf-8', 'ignore').strip()
    return value or None


def trace(conn, purpose=None, user=None):
    """End-to-end tracing for the DBA (V$SESSION, SYS_CONTEXT('USERENV', …), audit): MODULE, ACTION and
    CLIENT_IDENTIFIER. They travel with the next round trip, so this costs nothing."""
    conn.module = MODULE                       # first: setting MODULE clears ACTION
    conn.action = _text(purpose, 32)
    conn.client_identifier = _text(user.get('username') if isinstance(user, dict) else user, 64)


def _caller():
    """Name of the function that called connect(): the default ACTION, so every existing caller is traceable."""
    try:
        name = sys._getframe(2).f_code.co_name
    except ValueError:
        return None
    return None if name.startswith('<') else name


def _busy_message(found):
    wait = POOL['wait_timeout'] / 1000
    if found.busy >= found.max:
        return (f'All {found.max} Oracle connections of POPPYS AI are in use, and none became free within {wait:g} seconds. '
                'Try again in a moment. If this keeps happening, a long report or question may be holding them.')
    return (f'Oracle did not accept a connection within {wait:g} seconds. Check that Docker Desktop and the Oracle container '
            'are running, then try again.')


def connect(purpose=None, user=None, readonly=False):
    """A pooled Oracle connection; `with connect(...) as conn:` (or conn.close()) hands it back to the pool.

    purpose   what the session is for, its ACTION (≤32 bytes; default: the calling function's name)
    user      the signed-in user (a name or the session dict), its CLIENT_IDENTIFIER
    readonly  True on read paths (explorer, Ask AI, history, read-back): the read-only account when configured,
              else the main account (logged once)
    Every session carries MODULE 'POPPYS AI' and a call timeout of CALL_TIMEOUT_MS per round trip. When every
    session stays busy for POOL['wait_timeout'] ms, a RuntimeError with a plain message (HTTP 503) is raised."""
    found = pool(readonly)
    try:
        conn = found.acquire()
    except oracledb.Error as exc:
        if _code(exc) == 'DPY-4005':
            raise RuntimeError(_busy_message(found)) from None
        raise
    try:
        trace(conn, purpose or _caller(), user)
        conn.call_timeout = CALL_TIMEOUT_MS
    except BaseException:
        conn.close(); raise
    return conn


def close_pool():
    """Closes every pool (app shutdown, tests); sessions still checked out are closed too. connect() afterwards
    creates a new pool."""
    with _pools_lock:
        pools = list(_pools.values()); _pools.clear()
    for found in pools:
        try:
            found.close(force=True)
        except oracledb.Error as exc:
            log.warning('Could not close an Oracle connection pool cleanly (%s).', _code(exc) or type(exc).__name__)


def pool_stats():
    """[{account, readonly, opened, busy, max}] of the open pools, for health pages and scripts/check_oracle.py."""
    with _pools_lock:
        items = list(_pools.items())
    return [dict(account=key[1], readonly=key[0] == 'ro', opened=found.opened, busy=found.busy, max=found.max)
            for key, found in items]


def version_problem(version):
    """None when POPPYS AI supports this Oracle server version (conn.version), else the reason in plain English."""
    try:
        parts = tuple(int(x) for x in str(version).split('.')[:2])
    except ValueError:
        return f'Could not read the Oracle version ({version!r}).'
    if parts < MIN_VERSION:
        return (f'Oracle {version} is too old: POPPYS AI needs Oracle Database 12.1 or later (its queries use FETCH FIRST). '
                'Oracle 11g would need python-oracledb Thick mode and different SQL, which this build does not include.')
    return None


def current_mapping():
    from . import local_store
    return registry.effective(local_store.mapping_overrides())


def schema(conn, mapping=None):
    return OracleCatalog(conn).columns(registry.referenced_tables(mapping or current_mapping()))


def masters(conn, mapping=None, roles=None):
    """{role: [{id, label, cancelled}]} for the visible masters. With roles, only those masters, and one that
    cannot be listed (larger than the lookup limit, no grant) is left out instead of failing the others."""
    mapping = mapping or current_mapping(); cat = OracleCatalog(conn)
    visible = cat.columns(registry.referenced_tables(mapping))
    if roles is None:
        return {role: cat.masters(spec) for role, spec in mapping['masters'].items() if spec['table'] in visible}
    out = {}
    for role in roles:
        spec = mapping['masters'].get(role)
        if spec and spec['table'] in visible:
            try:
                out[role] = cat.masters(spec)
            except (ValueError, oracledb.Error):
                continue
    return out


def plan(conn, orders, filehash, mapping=None, required=()):
    return planner.plan(OracleCatalog(conn), orders, filehash, mapping or current_mapping(), required=required)


def read_result(cursor, jobid):
    cursor.execute('SELECT RESULT_JSON FROM PP_IMPORT_LOG WHERE JOBID=:id', id=jobid)
    row = cursor.fetchone()
    if not row:
        return None
    result = json.loads(row[0].read() if hasattr(row[0], 'read') else row[0]); result['already_committed'] = True
    return result


def pk_of(table, mapping):
    if table in mapping['tables']:
        return mapping['tables'][table]['key']
    for m in mapping['masters'].values():
        if m['table'] == table:
            return m['key']
    raise ValueError(f'{table}: not an allowlisted table.')


same = readback.same     # same(read_back, written, DATA_TYPE): by the column's type, never text as a number


# --- where saves may go -------------------------------------------------------------------------------
# POPPYS AI saves only to the local demo database (Docker on this computer, CI's throwaway Oracle). Previews and reads
# may connect anywhere ORACLE_DSN points; commit() refuses to write unless the DSN names only allowed hosts (loopback,
# or exactly the hosts in ORACLE_ALLOWED_HOSTS) and, when ORACLE_DB_IDENTITY is set, the session is in that database.
def dsn_hosts(dsn):
    """Host names or addresses an Oracle DSN connects to: Easy Connect (host:port/service, //host, tcps://host,
    [::1]:1521/x, host1,host2:1521/x) or a connect descriptor (HOST=...). [] when it names none (a tnsnames alias)."""
    text = str(dsn or '').strip()
    if '(' in text:
        return re.findall(r'(?i)\(\s*HOST\s*=\s*([^)\s]+)\s*\)', text)
    text = re.sub(r'^[a-z]+:(?=//)', '', text, flags=re.I).lstrip('/')
    if not re.search(r'[/:]', text):
        return []
    hosts = []
    for part in re.split(r'[,;]', re.split(r'[/?]', text, maxsplit=1)[0]):
        part = part.strip()
        bracketed = re.match(r'^\[([^\]]+)\]', part)
        hosts.append(bracketed.group(1) if bracketed else part.split(':')[0])
    return hosts if hosts and all(hosts) else []


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == 'localhost'


def write_problem(conn=None):
    """None when commit() may write through this configuration (and connection), else the plain reason."""
    dsn = setting('ORACLE_DSN', DEFAULT_DSN)
    hosts = dsn_hosts(dsn)
    allowed = [h.strip().lower() for h in setting('ORACLE_ALLOWED_HOSTS').split(',') if h.strip()]
    if not hosts:
        return (f'Nothing was saved: POPPYS AI cannot tell which computer ORACLE_DSN “{dsn}” points at. Write it as '
                'host:port/service in .env.poppys (for the local database 127.0.0.1:1521/FREEPDB1).')
    other = [h for h in hosts if (h.lower() not in allowed if allowed else not _loopback(h))]
    if other:
        return (f'Nothing was saved: POPPYS AI saves orders only to the database on this computer, and ORACLE_DSN points at '
                f'{", ".join(other)}. If that is a test database, an administrator can list it in ORACLE_ALLOWED_HOSTS in .env.poppys.')
    want = setting('ORACLE_DB_IDENTITY').strip().upper()
    if conn is not None and want:
        c = conn.cursor()
        c.execute("SELECT SYS_CONTEXT('USERENV','CON_NAME'), SYS_CONTEXT('USERENV','DB_UNIQUE_NAME') FROM dual")
        con, unique = (str(x or '').upper() for x in (c.fetchone() or ('', '')))
        if want not in (con, unique):
            return (f'Nothing was saved: .env.poppys allows saving only to the database {want} (ORACLE_DB_IDENTITY), but this '
                    f'connection is to {con or "?"} (DB_UNIQUE_NAME {unique or "?"}). Check ORACLE_DSN.')
    return None


def jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()[:10] if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if hasattr(v, 'read'):
        v = v.read()
    if isinstance(v, (bytes, bytearray)):
        return f'<binary, {len(v):,} bytes>'
    return v


def commit(draft, expected_hash, approver, mapping=None, required=()):
    """Re-plans inside the write lock with the same rules as the preview (including fields an administrator
    made required), so nothing the preview would refuse can be saved."""
    mapping = mapping or current_mapping()
    problem = write_problem()            # before connecting: a save never reaches a database it may not write
    if problem:
        raise RuntimeError(problem)
    with connect(purpose='save order', user=approver) as conn:
        problem = write_problem(conn)
        if problem:
            raise RuntimeError(problem)
        c = conn.cursor(); c.execute('SELECT ID FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10')
        if c.fetchone() is None:
            raise RuntimeError('Nothing was saved: the save lock (row 1 of POPPYS_APP.PP_WRITE_LOCK) is missing, so two saves could '
                               'overlap. Ask the database administrator to run: INSERT INTO POPPYS_APP.PP_WRITE_LOCK VALUES (1); COMMIT;')
        previous = read_result(c, draft['id'])
        if previous:
            return previous
        checked = plan(conn, draft['orders'], draft['filehash'], mapping, required)
        if checked['errors']:
            raise ValueError('; '.join(checked['errors']))
        if checked['hash'] != expected_hash:
            raise ValueError('Database or preview changed. Generate a fresh preview and review it before accepting.')
        registry_cols = schema(conn, mapping)
        refs = {}; inserted = []; reused = []; outorders = []
        try:
            for o in draft['orders']:
                c.execute('INSERT INTO PP_ORDER_KEYS(ORDER_REF,JOBID) VALUES (:ref,:job)', ref=normalize(o['order_ref']), job=draft['id'])
            for op in checked['operations']:
                table = op['table']; pk = pk_of(table, mapping)
                if op['action'] == 'reuse':
                    reused.append(dict(table=table, key=op['key'], pk=op['values'][pk], business=op.get('business'), values=op['values']))
                    continue
                c.execute('SELECT PP_RECORD_SEQ.nextval FROM dual'); ident = c.fetchone()[0]
                refs[op['key']] = ident
                vals = {}
                for col, v in op['values'].items():
                    if isinstance(v, planner.Ref):          # the generated ID of a row saved earlier in this transaction
                        if v['ref'] not in refs:
                            raise RuntimeError(f'{table}.{col} links to a record that is not saved yet. Nothing was saved; '
                                               'prepare the preview again.')
                        v = refs[v['ref']]
                    if v is not None and registry_cols[table][col]['type'] == 'DATE':
                        v = datetime.strptime(v, '%Y-%m-%d')
                    elif isinstance(v, float):
                        v = Decimal(str(v))
                    vals[col] = v
                cols = list(vals); binds = {f'v{i}': vals[col] for i, col in enumerate(cols)}
                c.execute(f'INSERT INTO {OWNER}.{table} ({",".join(cols)}) VALUES ({",".join(":" + k for k in binds)})', binds)
                c.execute(f'SELECT {",".join(cols)} FROM {OWNER}.{table} WHERE {pk}=:id', id=ident)
                rows = c.fetchall()
                if len(rows) != 1 or not all(same(a, b, registry_cols[table][col]['type']) for col, a, b in zip(cols, rows[0], vals.values())):
                    raise ValueError(f'{table}: inserted values did not match read-back.')
                inserted.append(dict(table=table, key=op['key'], pk=ident, business=op.get('business'), where=op.get('where'),
                                     values={col: jsonable(v) for col, v in zip(cols, rows[0])}))
                if table == 'ORDMAS':
                    outorders.append(dict(order_ref=vals['DOCID'], ordmasid=ident))
            types = {}          # {table: {column: DATA_TYPE}}: the history read-back compares by type (readback.same)
            for r in inserted + reused:
                types.setdefault(r['table'], {}).update({col: registry_cols[r['table']][col]['type'] for col in r['values']})
            result = dict(job_id=draft['id'], orders=outorders, rows=inserted, reused=reused, totals=checked['totals'],
                          reference_only=checked.get('reference_only', []), column_types=types,
                          committed_at=datetime.now().astimezone().isoformat(), approver=approver, already_committed=False)
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                      job=draft['id'], filehash=draft['filehash'], hash=expected_hash, who=approver,
                      payload=json.dumps(result, ensure_ascii=False))
            conn.commit(); return result
        except Exception:
            conn.rollback(); raise


def history(user=None):
    with connect(purpose='history', user=user, readonly=True) as conn:
        c = conn.cursor(); c.execute('SELECT JOBID,PLANHASH,APPROVER,COMMITTED_AT FROM PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC FETCH FIRST 100 ROWS ONLY')
        return [dict(job_id=j, plan_hash=h, approver=a, committed_at=d.isoformat()) for j, h, a, d in c]


def live_rows(conn, table, pk, ids, mapping):
    """Read complete current rows by primary key. Table and key are allowlisted by the registry."""
    if not ids:
        return {}
    if pk != pk_of(table, mapping):
        raise ValueError('Primary key mismatch.')
    c = conn.cursor(); out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        binds = {f'i{n}': v for n, v in enumerate(chunk)}
        c.execute(f'SELECT * FROM {OWNER}.{table} WHERE {pk} IN ({",".join(":" + k for k in binds)})', binds)
        names = [d[0] for d in c.description]
        for row in c:
            record = {n: jsonable(v) for n, v in zip(names, row)}
            out[record[pk]] = record
    return out
