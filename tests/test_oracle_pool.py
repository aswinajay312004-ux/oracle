"""Connection pools, end-to-end tracing and the read-only account (backend/oracle_store.py, scripts/check_oracle.py,
scripts/create_readonly_user.py). The first part runs anywhere with fakes; the second needs the local Oracle:

    POPPYS_TEST_ORACLE=1 .venv/bin/python -m pytest -q tests/test_oracle_pool.py

The read-only checks there also need POPPYS_RO (scripts/create_readonly_user.py); without it they are skipped."""
import os
import sys
import time
from pathlib import Path

import oracledb
import pytest
from oracledb.errors import _Error

from backend import oracle_store
from backend.insight import service
from backend.insight.query import QueryError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def ora(message):
    """A real oracledb error, as the driver raises it ('DPY-4024: …' gives full_code DPY-4024)."""
    return oracledb.DatabaseError(_Error(message))


TIMEOUT = 'DPY-4024: call timeout of 1000 ms exceeded'
CLOSED = 'DPY-4011: the database or network closed the connection'
EXHAUSTED = 'DPY-4005: timed out waiting for the connection pool to return a connection'


# --- fakes -----------------------------------------------------------------------------------------------
class TraceConn:
    """What the fake pool hands out: records attribute writes in order."""

    def __init__(self):
        object.__setattr__(self, 'writes', [])
        object.__setattr__(self, 'closed', 0)

    def __setattr__(self, name, value):
        self.writes.append((name, value)); object.__setattr__(self, name, value)

    def close(self):
        object.__setattr__(self, 'closed', self.closed + 1)


class FakePool:
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs; self.max = kwargs['max']; self.busy = 0; self.opened = 0; self.fail = None; self.closed = False
        FakePool.created.append(self)

    def acquire(self):
        if self.fail:
            raise self.fail
        self.busy += 1; self.opened = max(self.opened, self.busy)
        return TraceConn()

    def close(self, force=False):
        self.closed = force


@pytest.fixture
def fake_pool(monkeypatch):
    """oracle_store with fake settings and oracledb.create_pool replaced; the real pools (if any) are left alone."""
    cfg = dict(ORACLE_USER='POPPYS_APP', ORACLE_DSN='127.0.0.1:1599/NOWHERE')
    cfg['ORACLE_' + 'PASSWORD'] = 'unit-test-only'
    FakePool.created = []
    monkeypatch.setattr(oracle_store, 'setting', lambda key, default='': cfg.get(key, default))
    monkeypatch.setattr(oracle_store, '_pools', {})
    monkeypatch.setattr(oracle_store, '_noted', set())
    monkeypatch.setattr(oracledb, 'create_pool', lambda **kw: FakePool(**kw))
    return cfg


def test_driver_is_pinned_away_from_the_26_0_pool_hang():
    """python-oracledb issue #610: 26.0.x Thin pools can hang in WAIT mode; 4.0.2 is unaffected."""
    assert not oracledb.__version__.startswith('26.0')
    assert 'oracledb==4.0.2' in (ROOT / 'requirements.txt').read_text(encoding='utf-8').split()


def test_one_lazily_created_pool_with_the_decided_settings(fake_pool):
    assert FakePool.created == []                      # nothing at import time
    a = oracle_store.connect(); b = oracle_store.connect()
    assert a is not b and len(FakePool.created) == 1
    kw = FakePool.created[0].kwargs
    assert (kw['min'], kw['max'], kw['increment']) == (1, 4, 1)
    assert kw['getmode'] == oracledb.POOL_GETMODE_TIMEDWAIT and kw['wait_timeout'] == 3000
    assert kw['ping_interval'] > 0 and kw['timeout'] > 0, 'idle sessions are pinged and closed'
    assert kw['connectiontype'] is oracle_store.PooledConnection and kw['user'] == 'POPPYS_APP'
    assert ' ' not in kw['program']                    # the driver refuses spaces in program (DPY-3029)
    oracle_store.close_pool()
    assert FakePool.created[0].closed is True and oracle_store.pool_stats() == []
    oracle_store.connect()
    assert len(FakePool.created) == 2, 'a closed pool is created again on the next connect()'


def test_missing_configuration_is_still_a_plain_message(fake_pool):
    del fake_pool['ORACLE_' + 'PASSWORD']
    with pytest.raises(RuntimeError, match='bootstrap_local.py'):
        oracle_store.connect()


def test_every_acquire_sets_module_action_client_identifier_and_call_timeout(fake_pool):
    conn = oracle_store.connect(purpose='explorer', user=dict(username='alice', role='viewer'))
    assert (conn.module, conn.action, conn.client_identifier) == ('POPPYS AI', 'explorer', 'alice')
    assert conn.call_timeout == oracle_store.CALL_TIMEOUT_MS == 30000
    names = [n for n, _ in conn.writes]
    assert names.index('module') < names.index('action'), 'MODULE first: setting it clears ACTION'

    def saved_orders_page():
        return oracle_store.connect()
    conn = saved_orders_page()
    assert conn.action == 'saved_orders_page', 'without a purpose, the calling function is the ACTION'
    assert conn.client_identifier is None, 'no user: cleared with None, never a previous user'


def test_tracing_never_sends_empty_strings_and_respects_oracle_lengths(fake_pool):
    """'' makes python-oracledb 4.0.2 Thin lose the session (DPY-4011 on 23ai); None clears safely."""
    conn = TraceConn()
    oracle_store.trace(conn, '', '   ')
    assert conn.action is None and conn.client_identifier is None and '' not in [v for _, v in conn.writes]
    conn = oracle_store.connect(purpose='', user='')
    assert conn.action == 'test_tracing_never_sends_empty_s' and conn.client_identifier is None   # 32 bytes of the caller
    conn = oracle_store.connect(purpose='p' * 50, user='ü' * 40)
    assert len(conn.action.encode()) == 32 and len(conn.client_identifier.encode()) <= 64
    assert conn.client_identifier == 'ü' * 32, 'cut on a character boundary'


def test_exhausted_pool_is_a_plain_runtime_error(fake_pool):
    p = oracle_store.pool(); p.busy = p.max; p.fail = ora(EXHAUSTED)
    with pytest.raises(RuntimeError) as err:
        oracle_store.connect()
    assert 'All 4 Oracle connections' in str(err.value) and 'Try again' in str(err.value) and 'DPY' not in str(err.value)
    p.busy = 0          # timed out while not all sessions were busy: the database did not let a new one in
    with pytest.raises(RuntimeError, match='did not accept a connection within 3 seconds'):
        oracle_store.connect()
    p.fail = ora('DPY-6005: cannot connect to database')
    with pytest.raises(oracledb.DatabaseError):   # other errors keep their type (the existing 503 handler)
        oracle_store.connect()


def test_exhausted_pool_answers_http_503(fake_pool):
    from fastapi.testclient import TestClient
    from backend import main
    p = oracle_store.pool(); p.busy = p.max; p.fail = ora(EXHAUSTED)
    main.app.dependency_overrides[main.authenticated] = lambda: dict(username='alice', role='admin')
    try:
        r = TestClient(main.app).get('/api/history/some-job')
    finally:
        main.app.dependency_overrides.pop(main.authenticated, None)
    assert r.status_code == 503 and 'All 4 Oracle connections' in r.text


def test_readonly_falls_back_to_the_main_account_and_says_so_once(fake_pool, caplog):
    with caplog.at_level('INFO', logger='poppys.oracle'):
        oracle_store.connect(readonly=True); oracle_store.connect(readonly=True)
    assert caplog.text.count('No read-only Oracle account is configured') == 1
    assert [p.kwargs['user'] for p in FakePool.created] == ['POPPYS_APP']
    fake_pool.update(ORACLE_RO_USER='POPPYS_RO'); fake_pool['ORACLE_RO_' + 'PASSWORD'] = 'unit-test-ro'
    oracle_store.connect(readonly=True); oracle_store.connect()
    assert [p.kwargs['user'] for p in FakePool.created] == ['POPPYS_APP', 'POPPYS_RO'], 'one more pool, for the RO account'
    assert [s['readonly'] for s in oracle_store.pool_stats()] == [False, True]


# --- the pooled connection itself (real PooledConnection over a fake driver implementation) --------------------
class Impl:
    def __init__(self, healthy=True):
        self.healthy = healthy

    def get_is_healthy(self):
        return self.healthy

    def create_cursor_impl(self, scrollable):
        return type('CursorImpl', (), {'close': lambda self, in_del=False: None})()


class DriverPool:
    """Stands in for oracledb.ConnectionPool behind a PooledConnection."""

    def __init__(self, drop_fails=False):
        self.returned = []; self.dropped = []; self.drop_fails = drop_fails
        self._impl = self

    def return_connection(self, impl, in_del=False):
        self.returned.append(impl)

    def drop(self, conn):
        if self.drop_fails:
            raise ora('DPY-1001: not connected to database')
        self.dropped.append(conn); conn._impl = None


def pooled(healthy=True, drop_fails=False):
    conn = object.__new__(oracle_store.PooledConnection)
    conn._impl = Impl(healthy); conn._pool = DriverPool(drop_fails)
    return conn, conn._pool


def test_healthy_connection_goes_back_to_the_pool():
    conn, p = pooled()
    impl = conn._impl
    with conn:
        pass
    assert p.returned == [impl] and p.dropped == []
    with pytest.raises(oracledb.InterfaceError, match='DPY-1001'):
        conn.close()                    # as for any closed connection


def test_a_call_timeout_the_caller_handled_still_drops_the_session(monkeypatch):
    """The live finder treats a timed-out probe as 'not found' and goes on; the session must not be reused."""
    def timeout(self, *args, **kwargs):
        raise ora(TIMEOUT)
    monkeypatch.setattr(oracledb.Cursor, 'execute', timeout)
    conn, p = pooled()
    cur = conn.cursor()
    assert isinstance(cur, oracle_store._WatchedCursor)
    try:
        cur.execute('SELECT 1 FROM DUAL')
    except oracledb.DatabaseError:
        pass
    conn.close()
    assert p.dropped == [conn] and p.returned == [] and conn._poppys_broken == 'DPY-4024'


def test_an_error_leaving_with_is_traced_back_to_a_broken_session():
    conn, p = pooled()
    with pytest.raises(QueryError):
        with conn:
            try:
                raise ora(TIMEOUT)
            except oracledb.DatabaseError:
                raise service.TimedOut(1) from None   # what OracleExecutor raises: DPY-4024 kept as __context__
    assert p.dropped == [conn]


def test_ordinary_errors_keep_the_session_and_dead_ones_are_dropped(monkeypatch):
    def missing(self, *args, **kwargs):
        raise ora('ORA-00942: table or view does not exist')
    monkeypatch.setattr(oracledb.Cursor, 'execute', missing)
    conn, p = pooled()
    with pytest.raises(oracledb.DatabaseError):
        with conn:
            conn.cursor().execute('SELECT * FROM NOPE')
    assert p.dropped == [] and len(p.returned) == 1
    conn, p = pooled(healthy=False)     # the driver marked it dead (DPY-4011): dropped even without an error seen
    conn.close()
    assert p.dropped == [conn]


def test_a_failing_drop_falls_back_to_a_plain_release():
    conn, p = pooled(healthy=False, drop_fails=True)
    conn.close()
    assert p.dropped == [] and len(p.returned) == 1 and conn._impl is None


# --- the read-only executor after a lost session -----------------------------------------------------------------
class LostConn:
    """A session the driver closed during a call: every later call fails, is_healthy() is False."""

    def __init__(self, delay):
        self.call_timeout = 0; self.delay = delay

    def cursor(self):
        conn = self

        class Cursor:
            arraysize = 0

            def execute(self, sql, binds=None):
                if sql != 'SET TRANSACTION READ ONLY':
                    time.sleep(conn.delay)
                    raise ora(CLOSED)
        return Cursor()

    def rollback(self):
        raise ora('DPY-1001: not connected to database')

    def is_healthy(self):
        return False


def test_a_session_lost_at_the_time_limit_is_reported_as_the_time_limit():
    ex = service.OracleExecutor(LostConn(delay=0.02), timeout_ms=10)
    with pytest.raises(service.TimedOut, match='more than 0.01 seconds'):
        ex('SELECT 1 FROM DUAL', {}, 1)


def test_a_session_lost_before_the_time_limit_is_a_database_error_not_a_hidden_rollback_error():
    ex = service.OracleExecutor(LostConn(delay=0), timeout_ms=20000)
    with pytest.raises(oracledb.DatabaseError, match='DPY-4011'):
        ex('SELECT 1 FROM DUAL', {}, 1)


# --- preflight and the read-only account script ---------------------------------------------------------------
def test_version_preflight():
    assert oracle_store.version_problem('23.26.3.0.0') is None and oracle_store.version_problem('12.1.0.2.0') is None
    assert '12.1 or later' in oracle_store.version_problem('11.2.0.4.0')
    assert oracle_store.version_problem('unknown')


class DictConn:
    """Answers the dictionary queries scripts/check_oracle.py makes."""

    def __init__(self, username, grants, system=('CREATE SESSION',), tables=('ORDMAS',), read_only='NO'):
        self.username = username; self.answers = dict(grants=grants, system=system, tables=tables, read_only=read_only)

    def cursor(self):
        conn = self

        class Cursor:
            description = None

            def execute(self, sql, binds=None, **kw):
                a = conn.answers
                if 'all_tab_privs' in sql:
                    self.rows = list(a['grants'])
                elif 'session_privs' in sql:
                    self.rows = [(p,) for p in a['system']]
                elif 'all_tab_columns' in sql:
                    self.rows = [(t,) for t in a['tables']]
                elif 'user_users' in sql:
                    self.description = [('USERNAME',), ('READ_ONLY',)]; self.rows = [(conn.username, a['read_only'])]
                else:
                    self.rows = [(0,)]

            def __iter__(self):
                return iter(self.rows)

            def fetchone(self):
                return self.rows[0]
        return Cursor()


def test_preflight_fails_a_read_only_account_that_can_insert(capsys):
    import check_oracle
    report = check_oracle.Report()
    grants = [('GARMENT_DEMO', 'ORDMAS', 'READ'), ('GARMENT_DEMO', 'ORDMAS', 'INSERT'), ('GARMENT_DEMO', 'GARMENT_DEMO', 'INHERIT PRIVILEGES')]
    check_oracle.check_readonly(DictConn('POPPYS_RO', grants), report, 'GARMENT_DEMO', 'POPPYS_APP', {'ORDMAS'})
    out = capsys.readouterr().out
    assert report.failed == 1 and 'INSERT on GARMENT_DEMO.ORDMAS' in out and 'INHERIT' not in out
    report = check_oracle.Report()
    check_oracle.check_readonly(DictConn('POPPYS_RO', [('GARMENT_DEMO', 'ORDMAS', 'READ'), ('POPPYS_APP', 'PP_IMPORT_LOG', 'READ')],
                                         read_only='YES'), report, 'GARMENT_DEMO', 'POPPYS_APP', {'ORDMAS'})
    out = capsys.readouterr().out
    assert report.failed == 0 and 'can only read' in out and 'READ ONLY user' in out


def test_preflight_fails_a_main_account_that_can_update_or_delete(capsys, monkeypatch):
    import check_oracle
    from backend.erp import catalog
    monkeypatch.setattr(catalog.OracleCatalog, 'columns', lambda self, tables: {})
    monkeypatch.setattr(catalog.OracleCatalog, 'insertable', lambda self, tables: {'ORDMAS'})
    report = check_oracle.Report()
    check_oracle.check_main(DictConn('POPPYS_APP', [('GARMENT_DEMO', 'ORDMAS', 'INSERT'), ('GARMENT_DEMO', 'ORDMAS', 'UPDATE')],
                                     system=('CREATE SESSION', 'DELETE ANY TABLE')), report, 'GARMENT_DEMO')
    out = capsys.readouterr().out
    assert report.failed == 1 and 'UPDATE on ORDMAS' in out and 'DELETE ANY TABLE' in out
    report = check_oracle.Report()
    check_oracle.check_main(DictConn('POPPYS_APP', [('GARMENT_DEMO', 'ORDMAS', 'INSERT'), ('GARMENT_DEMO', 'ORDMAS', 'SELECT')]),
                            report, 'GARMENT_DEMO')
    assert report.failed == 0 and 'cannot UPDATE or DELETE' in capsys.readouterr().out


@pytest.fixture
def ro_script(tmp_path, monkeypatch):
    import create_readonly_user as script
    config = tmp_path / '.env.poppys'
    config.write_text('ORACLE_USER=POPPYS_APP\n' + 'ORACLE_PASSWORD' + '=' + 'unit-test-only\n', encoding='utf-8')
    calls = []; state = dict(ro_exists=False)

    def sqlplus(sql):
        calls.append(sql)
        if "username='POPPYS_APP'" in sql or ('PP_IMPORT_LOG' in sql and 'dba_tables' in sql):
            return '1'
        if "username='POPPYS_RO'" in sql:
            return '1' if state['ro_exists'] else '0'
        if 'create user' in sql:
            assert 'ORACLE_RO_' + 'PASSWORD' in config.read_text(encoding='utf-8'), 'saved before the account is created'
            state['ro_exists'] = True
        return 'TABLES=19\nREADONLY=yes' if 'dbms_output' in sql else ''
    monkeypatch.setattr(script, 'CONFIG', config); monkeypatch.setattr(script, 'sqlplus', sqlplus)
    return script, config, calls, state


def test_readonly_script_creates_the_account_without_printing_its_password(ro_script, capsys):
    script, config, calls, _ = ro_script
    script.main()
    from dotenv import dotenv_values
    saved = dotenv_values(config)
    secret = saved['ORACLE_RO_' + 'PASSWORD']
    assert saved['ORACLE_RO_USER'] == 'POPPYS_RO' and saved['ORACLE_USER'] == 'POPPYS_APP' and len(secret) == 50
    assert secret not in capsys.readouterr().out
    ddl = '\n'.join(calls).lower()
    assert 'grant create session to poppys_ro' in ddl and 'grant read on' in ddl and 'read only' in ddl
    for word in ('grant insert', 'grant update', 'grant delete', 'any table', 'grant resource', 'grant dba'):
        assert word not in ddl, word


def test_readonly_script_never_resets_an_existing_account(ro_script, capsys):
    script, config, calls, state = ro_script
    state['ro_exists'] = True
    with pytest.raises(SystemExit, match='will not be reset'):
        script.main()
    assert not any('create user' in c or 'identified' in c for c in calls)
    config.write_text(config.read_text(encoding='utf-8') + 'ORACLE_RO_USER=POPPYS_RO\n' + 'ORACLE_RO_' + 'PASSWORD' + '=' + 'unit-test-ro\n',
                      encoding='utf-8')
    calls.clear(); script.main()        # configured and present: only brings the READ grants in step
    assert not any('create user' in c or 'identified' in c for c in calls)
    assert 'password was not changed' in capsys.readouterr().out


# --- real Oracle (opt-in) ------------------------------------------------------------------------------------
oracle_only = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
REFUSED = ('ORA-01031', 'ORA-41900', 'ORA-28194')   # insufficient privileges (23ai: "missing X privilege"), read-only user


def refused(conn, sql):
    with pytest.raises(oracledb.DatabaseError) as err:
        conn.cursor().execute(sql)
    conn.rollback()
    code = oracle_store._code(err.value)
    assert code in REFUSED, f'{sql}: {code}'
    return code


def session(conn):
    c = conn.cursor()
    c.execute("""SELECT SYS_CONTEXT('USERENV','SESSIONID'), SYS_CONTEXT('USERENV','MODULE'), SYS_CONTEXT('USERENV','ACTION'),
                        SYS_CONTEXT('USERENV','CLIENT_IDENTIFIER'), USER FROM DUAL""")
    return c.fetchone()


@oracle_only
def test_real_main_account_cannot_update_or_delete():
    with oracle_store.connect(purpose='privilege test') as conn:
        refused(conn, 'UPDATE GARMENT_DEMO.ORDMAS SET DOCID = DOCID WHERE 1 = 0')
        refused(conn, 'DELETE FROM GARMENT_DEMO.ORDMAS WHERE 1 = 0')
        refused(conn, 'DELETE FROM GARMENT_DEMO.ORDSIZE WHERE 1 = 0')


@oracle_only
def test_real_readonly_account_can_read_but_never_write():
    if not oracle_store.readonly_configured():
        pytest.skip('No read-only account: run scripts/create_readonly_user.py')
    with oracle_store.connect(purpose='privilege test', readonly=True) as conn, oracle_store.connect() as main_conn:
        assert session(conn)[4] != session(main_conn)[4]
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM GARMENT_DEMO.ORDMAS'); assert c.fetchone()[0] >= 0
        c.execute('SELECT COUNT(*) FROM PP_IMPORT_LOG'); assert c.fetchone()[0] >= 0      # import history, via its synonym
        refused(conn, "INSERT INTO GARMENT_DEMO.UNITMAS (UNITMASID) VALUES (-1)")
        refused(conn, 'UPDATE GARMENT_DEMO.ORDMAS SET DOCID = DOCID WHERE 1 = 0')
        refused(conn, 'DELETE FROM GARMENT_DEMO.ORDMAS WHERE 1 = 0')
        refused(conn, "INSERT INTO PP_IMPORT_LOG (JOBID) VALUES ('x')")
        refused(conn, 'SELECT 1 FROM GARMENT_DEMO.ORDMAS WHERE ROWNUM = 1 FOR UPDATE')   # READ, not SELECT: no row locks
        # Both accounts see the same dictionary, so Ask AI and the explorer answer the same on either.
        a, b = service.harvest(conn), service.harvest(main_conn)
        assert (a['physical'], a['fks'], a['indexed']) == (b['physical'], b['fks'], b['indexed'])
    assert isinstance(oracle_store.history(), list)          # history() reads through the read-only account


@oracle_only
def test_real_module_action_and_client_identifier_reach_the_session_and_are_reset_on_reuse():
    oracle_store.close_pool()
    with oracle_store.connect(purpose='pool test', user='tester') as conn:
        first = session(conn)
        assert first[1:4] == ('POPPYS AI', 'pool test', 'tester')

    def next_request():
        return oracle_store.connect()
    with next_request() as conn:
        again = session(conn)
    assert again[0] == first[0], 'min=1: the same session is reused'
    assert again[1:4] == ('POPPYS AI', 'next_request', None), 'the previous user and action do not leak'


@oracle_only
def test_real_call_timeout_raises_dpy_4024_and_the_pool_still_works():
    with oracle_store.connect(purpose='timeout test') as conn:
        before = session(conn)[0]
        conn.call_timeout = 1000
        started = time.monotonic()
        with pytest.raises(oracledb.DatabaseError) as err:
            conn.cursor().execute('BEGIN DBMS_SESSION.SLEEP(1.5); END;')   # ends within the driver's recovery window
        assert oracle_store._code(err.value) == 'DPY-4024' and time.monotonic() - started < 5
        assert conn._poppys_broken == 'DPY-4024'
    with oracle_store.connect() as conn:
        assert session(conn)[0] != before, 'the timed-out session was dropped, not reused'
        c = conn.cursor(); c.execute('SELECT 1 FROM DUAL'); assert c.fetchone() == (1,)
        assert conn.call_timeout == oracle_store.CALL_TIMEOUT_MS


@oracle_only
def test_real_session_lost_after_a_timeout_is_dropped_and_the_pool_still_works():
    """PL/SQL that ignores the break past the recovery window: the driver gives up (DPY-4011, session closed)."""
    from backend.config import OWNER
    with oracle_store.connect(purpose='timeout test') as conn:
        conn.call_timeout = 1000
        with pytest.raises(oracledb.DatabaseError) as err:
            conn.cursor().execute('BEGIN DBMS_SESSION.SLEEP(3); END;')
        assert oracle_store._code(err.value) in ('DPY-4011', 'DPY-4024')
    with oracle_store.connect() as conn:
        ex = service.OracleExecutor(conn)
        assert ex(f'SELECT COUNT(*) FROM {OWNER}.UNITMAS', {}, 1)[0][0] >= 0


@oracle_only
def test_real_exhausted_pool_refuses_within_about_three_seconds_then_recovers():
    oracle_store.close_pool()
    held = []
    try:
        for readonly in (False, True):     # both pools, so the HTTP check below is refused whichever account it uses
            for _ in range(oracle_store.POOL['max'] if readonly is False or oracle_store.readonly_configured() else 0):
                held.append(oracle_store.connect(purpose='hold', readonly=readonly))
        started = time.monotonic()
        with pytest.raises(RuntimeError, match='All 4 Oracle connections'):
            oracle_store.connect(purpose='one too many')
        assert 2.5 <= time.monotonic() - started < 5
        from fastapi.testclient import TestClient
        from backend import main
        main.app.dependency_overrides[main.authenticated] = lambda: dict(username='alice', role='admin')
        try:
            started = time.monotonic(); r = TestClient(main.app).get('/api/history/some-job')
        finally:
            main.app.dependency_overrides.pop(main.authenticated, None)
        assert r.status_code == 503 and 'All 4 Oracle connections' in r.text and time.monotonic() - started < 5
    finally:
        for conn in held:
            conn.close()
    with oracle_store.connect() as conn:
        c = conn.cursor(); c.execute('SELECT 1 FROM DUAL'); assert c.fetchone() == (1,)
    oracle_store.close_pool()
    assert oracle_store.pool_stats() == []
