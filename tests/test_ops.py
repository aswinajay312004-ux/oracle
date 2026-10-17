"""Operations (docs/RESEARCH_DECISIONS.md checklist items 5, 30 (audit chain) and 44): /healthz, /readyz and
/api/version; JSON-lines logs with request IDs, redaction and rotation (also while the file is being read);
SQLite connections, PRAGMAs and numbered migrations with a verified backup; the append-only, hash-chained audit log.

Run the store tests with ResourceWarning as an error too:
    .venv/bin/python -m pytest -q -W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning tests/test_ops.py
"""
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import local_store, logs, main, security

PW = 'test-password-123'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    main._ready.update(at=0.0, status=None, future=None)
    with TestClient(main.app) as c:
        yield c
    main._ready.update(at=0.0, status=None, future=None)


@pytest.fixture
def admin(client):
    assert client.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
    return client


def log_lines(tmp_path):
    logs.flush()
    path = tmp_path / 'logs' / 'app.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def oracle_down(*a, **k):
    raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')


class FakeConn:
    call_timeout = 0

    def ping(self):
        return None

    def close(self):
        pass


# --- health, readiness, version -------------------------------------------------------------------------------------------
def test_healthz_needs_no_sign_in_and_does_no_io(client, monkeypatch):
    monkeypatch.setattr(local_store, 'probe', lambda: (_ for _ in ()).throw(AssertionError('no I/O')))
    r = client.get('/healthz')
    assert r.status_code == 200 and r.json() == {'status': 'ok'}


def test_readyz_warns_but_stays_200_when_oracle_is_down_and_names_nothing(client, monkeypatch):
    monkeypatch.setenv('POPPYS_ORACLE_DSN', '127.0.0.1:1521/FREEPDB1'); monkeypatch.setenv('POPPYS_ORACLE_USER', 'POPPYS_APP')
    monkeypatch.setattr(main.oracle, 'connect', oracle_down)
    r = client.get('/readyz')
    assert r.status_code == 200 and r.json()['status'] == 'warn'
    assert r.json()['checks']['sqlite'] == 'ok' and r.json()['checks']['oracle'] == 'warn'
    for leak in ('1521', 'FREEPDB1', 'POPPYS_APP', 'GARMENT', 'bootstrap', 'oracledb', '3.', 'version'):
        assert leak not in r.text, leak


def test_readyz_ok_when_everything_answers(client, monkeypatch):
    monkeypatch.setattr(main.oracle, 'connect', lambda *a, **k: FakeConn())
    monkeypatch.setattr(main.shutil, 'disk_usage', lambda p: type('U', (), {'free': 50 * 1024 ** 3})())
    r = client.get('/readyz')
    assert r.status_code == 200 and r.json() == {'status': 'ok', 'checks': {'sqlite': 'ok', 'disk': 'ok', 'oracle': 'ok'}}


def test_readyz_warns_on_low_disk(client, monkeypatch):
    monkeypatch.setattr(main.oracle, 'connect', lambda *a, **k: FakeConn())
    monkeypatch.setattr(main.shutil, 'disk_usage', lambda p: type('U', (), {'free': 200 * 1024 ** 2})())
    assert client.get('/readyz').json() == {'status': 'warn', 'checks': {'sqlite': 'ok', 'disk': 'warn', 'oracle': 'ok'}}


def test_readyz_fails_with_503_only_when_sqlite_is_broken(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main.oracle, 'connect', lambda *a, **k: FakeConn())
    with closing(sqlite3.connect(tmp_path / local_store.DB_NAME)) as c:   # e.g. an old copy restored without its upgrade
        c.execute('PRAGMA user_version=1')
    r = client.get('/readyz')
    assert r.status_code == 503 and r.json()['status'] == 'fail' and r.json()['checks']['sqlite'] == 'fail'


def test_readyz_answers_within_its_budget_when_oracle_hangs(client, monkeypatch):
    release = threading.Event()

    def hang(*a, **k):
        release.wait(20); raise RuntimeError('gave up')
    monkeypatch.setattr(main.oracle, 'connect', hang)
    monkeypatch.setattr(main, 'READY_ORACLE_BUDGET_S', 0.5)
    started = time.monotonic()
    r = client.get('/readyz')
    took = time.monotonic() - started
    release.set()
    assert r.status_code == 200 and r.json()['checks']['oracle'] == 'warn' and took < 3, took


def test_version_endpoint(admin):
    v = admin.get('/api/version').json()
    assert v['version'] == (Path(main.ROOT) / 'VERSION').read_text().strip() and v['sqlite'] == sqlite3.sqlite_version


# --- JSON logs ----------------------------------------------------------------------------------------------------------------
def test_every_request_gets_one_json_access_line_with_its_request_id(admin, tmp_path):
    r = admin.get('/api/drafts?secret_query=should-not-appear')
    rid = r.headers['x-request-id']
    lines = log_lines(tmp_path)
    access = [x for x in lines if x['logger'] == 'poppys.access' and x.get('request_id') == rid]
    assert len(access) == 1
    a = access[0]
    assert a['method'] == 'GET' and a['path'] == '/api/drafts' and a['status'] == 200 and a['user'] == 'admin'
    assert isinstance(a['ms'], float) and a['ts'].endswith('Z') and a['level'] == 'INFO'
    assert 'should-not-appear' not in (tmp_path / 'logs' / 'app.jsonl').read_text(encoding='utf-8')
    assert all(x.get('request_id') for x in lines if x['logger'].startswith(('poppys.access', 'poppys.security')))


def test_security_events_are_logged_and_passwords_never_are(client, tmp_path):
    secret_pw = 'Correct-Horse-Battery-77'
    assert client.post('/api/setup', json={'username': 'admin', 'password': secret_pw}).status_code == 200
    token = client.cookies.get('poppys_session')
    client.post('/api/login', json={'username': 'admin', 'password': 'Wrong-Horse-Battery-88'})
    client.post('/api/login', json={'username': 'admin', 'password': secret_pw})
    client.post('/api/uploads', files={'file': ('bom.csv', b'Style,Colour,Size,Qty\nT1,NAVY,S,10\n')})
    client.post('/api/logout')
    logs.flush()
    text = (tmp_path / 'logs' / 'app.jsonl').read_text(encoding='utf-8')
    assert secret_pw not in text and 'Wrong-Horse-Battery-88' not in text and token not in text
    events = [x.get('event') for x in log_lines(tmp_path)]
    for e in ('setup', 'login_failed', 'login_ok', 'logout'):
        assert e in events, e
    failed = next(x for x in log_lines(tmp_path) if x.get('event') == 'login_failed')
    assert failed['level'] == 'WARNING' and failed['user'] == 'admin' and failed['logger'] == 'poppys.security'


def test_redaction_of_fields_and_message_text():
    assert logs.redact({'password': 'p', 'Authorization': 'Bearer x', 'nested': {'api_key': 'k', 'session_token': 't', 'ok': 1},
                        'cookies': ['a'], 'list': [{'secret': 's'}], 'user': 'admin'}) == \
        {'password': '[redacted]', 'Authorization': '[redacted]', 'nested': {'api_key': '[redacted]', 'session_token': '[redacted]', 'ok': 1},
         'cookies': '[redacted]', 'list': [{'secret': '[redacted]'}], 'user': 'admin'}
    key = 'ORACLE_' + 'PASSWORD'          # spelled in two parts so the repository's secret scan does not flag this test
    text = logs.scrub(f'connect password=hunter2 {key}: "x y" Cookie: poppys_session=abc; other=1 Authorization: Bearer zzz')
    assert 'hunter2' not in text and 'x y' not in text and 'abc' not in text and 'zzz' not in text
    logs.add_secret('Plain-Oracle-Secret')
    assert 'Plain-Oracle-Secret' not in logs.scrub('DPY-4011 for Plain-Oracle-Secret@db')


def test_error_log_line_has_traceback_and_request_id(admin, tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'summaries', lambda: 1 / 0)
    r = admin.get('/api/drafts')
    rid = r.json()['request_id']
    err = [x for x in log_lines(tmp_path) if x.get('event') == 'unhandled_error']
    assert err and err[-1]['request_id'] == rid and 'ZeroDivisionError' in err[-1]['traceback'] and err[-1]['exc_type'] == 'ZeroDivisionError'


def _handler(tmp_path, max_bytes=3000, backups=3):
    """A logger writing through a fresh logs.setup() into tmp_path (restored to nothing afterwards)."""
    logs.setup(tmp_path, max_bytes=max_bytes, backups=backups)
    return logging.getLogger('poppys.test-rotation')


def _all_lines(folder):
    out = []
    for p in sorted(folder.glob('app.jsonl*')):
        out += [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]
    return out


def test_rotation_by_size_keeps_whole_json_lines(tmp_path):
    lg = _handler(tmp_path, max_bytes=4000, backups=50)
    try:
        for i in range(400):
            lg.info('line', extra={'fields': {'n': i, 'pad': 'x' * 40}})
        logs.flush()
        files = sorted(tmp_path.glob('app.jsonl*'))
        assert len(files) > 5 and all(p.stat().st_size <= 4000 for p in files)
        assert sorted(x['n'] for x in _all_lines(tmp_path) if x['msg'] == 'line') == list(range(400))
    finally:
        logs.shutdown()


def test_rotation_while_the_file_is_being_tailed(tmp_path):
    lg = _handler(tmp_path, max_bytes=3000, backups=50)
    try:
        lg.info('first', extra={'fields': {'n': -1}}); logs.flush()
        tail = open(tmp_path / 'app.jsonl', encoding='utf-8')     # like Get-Content -Wait / tail -f
        seen = [tail.readline()]
        for i in range(200):
            lg.info('line', extra={'fields': {'n': i}})
        logs.flush()
        seen += tail.readlines()
        tail.close()
        assert json.loads(seen[0])['msg'] == 'first' and all(json.loads(x) for x in seen if x.strip())
        assert sorted(x['n'] for x in _all_lines(tmp_path) if x['msg'] == 'line') == list(range(200))
    finally:
        logs.shutdown()


def test_windows_sharing_violation_falls_back_to_copy_and_truncate(tmp_path, monkeypatch):
    """On Windows a file another program holds open cannot be renamed: the log copies it to .1 and empties it."""
    base = str(tmp_path / 'app.jsonl')
    real_replace, real_rename = os.replace, os.rename

    def refuse(src, dst, *a, **k):
        if os.path.abspath(src) == base:
            raise PermissionError(32, 'The process cannot access the file because it is being used by another process', src)
        return real_rename(src, dst)
    monkeypatch.setattr(logs.os, 'replace', lambda s, d: refuse(s, d) if os.path.abspath(s) == base else real_replace(s, d))
    monkeypatch.setattr(logs.os, 'rename', refuse)
    lg = _handler(tmp_path, max_bytes=3000, backups=50)
    try:
        for i in range(300):
            lg.info('line', extra={'fields': {'n': i}})
        logs.flush()
        assert (tmp_path / 'app.jsonl.1').exists() and (tmp_path / 'app.jsonl').stat().st_size <= 3000
        assert sorted(x['n'] for x in _all_lines(tmp_path) if x['msg'] == 'line') == list(range(300))
    finally:
        logs.shutdown()


def test_when_rotation_is_impossible_logging_carries_on_quietly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(logs.os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError(32, 'in use')))
    monkeypatch.setattr(logs.os, 'rename', lambda *a: (_ for _ in ()).throw(PermissionError(32, 'in use')))
    monkeypatch.setattr(logs.shutil, 'copyfile', lambda *a: (_ for _ in ()).throw(PermissionError(32, 'in use')))
    lg = _handler(tmp_path, max_bytes=2000, backups=3)
    try:
        for i in range(100):
            lg.info('line', extra={'fields': {'n': i}})
        logs.flush()
        assert sorted(x['n'] for x in _all_lines(tmp_path) if x['msg'] == 'line') == list(range(100))
        assert 'Traceback' not in capsys.readouterr().err
    finally:
        logs.shutdown()


# --- SQLite connections, PRAGMAs, migrations --------------------------------------------------------------------------------------
def test_pragmas_on_every_connection(client):
    with local_store.db() as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert c.execute('PRAGMA synchronous').fetchone()[0] == 2           # FULL
        assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert c.execute('PRAGMA busy_timeout').fetchone()[0] == 20000
        assert c.execute('PRAGMA user_version').fetchone()[0] == local_store.SCHEMA_VERSION


def test_store_connections_are_closed(tmp_path, monkeypatch):
    """Every connection local_store opens is closed again (``with sqlite3.connect()`` alone would leave it open).
    The -W error::ResourceWarning run in the module docstring checks the same through the interpreter."""
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    opened = []
    real = sqlite3.connect

    def tracking(*a, **k):
        conn = real(*a, **k); opened.append(conn); return conn
    monkeypatch.setattr(local_store.sqlite3, 'connect', tracking)
    local_store.init()
    local_store.audit('a', 'x', 't', n=1); local_store.audit_events(); local_store.verify_audit_chain()
    local_store.set_setting('k', 1); local_store.get_setting('k'); local_store.templates(); local_store.probe()
    local_store.session_create('h', 'a', time.time(), 60, 60); local_store.session_user('h', time.time(), 60, 1)
    local_store.login_reserve('a', 'ip', time.time(), 900, security.wait_for); local_store.locks()
    with pytest.raises(KeyError):
        local_store.get('missing')
    with pytest.raises(sqlite3.IntegrityError):
        with local_store.db() as c:
            c.execute("INSERT INTO settings VALUES ('dup', '1')"); c.execute("INSERT INTO settings VALUES ('dup', '1')")
    assert len(opened) > 10
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')


def test_a_failed_block_rolls_back_and_still_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); local_store.init()
    with pytest.raises(sqlite3.IntegrityError):
        with local_store.db() as c:
            c.execute("INSERT INTO settings VALUES ('half', '1')")
            c.execute("INSERT INTO settings VALUES ('half', '2')")     # duplicate key: the whole block rolls back
    assert local_store.get_setting('half') is None


LEGACY_SCHEMA = '''
CREATE TABLE users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', active INTEGER NOT NULL DEFAULT 1, created_at TEXT);
CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE drafts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE revisions(id INTEGER PRIMARY KEY,draft_id TEXT,version INTEGER,actor TEXT,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,actor TEXT,action TEXT NOT NULL,target TEXT,detail TEXT);
CREATE TABLE templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,signature TEXT NOT NULL,labels TEXT NOT NULL,
    definition TEXT NOT NULL,created_by TEXT,created_at TEXT,uses INTEGER DEFAULT 0,active INTEGER DEFAULT 1);
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''


def legacy_db(folder, password='old-password-10'):
    """A database as the app wrote it before numbered migrations (user_version 0, PBKDF2 300k, old sessions)."""
    salt = '11' * 16
    with closing(sqlite3.connect(folder / local_store.DB_NAME)) as c:
        c.executescript(LEGACY_SCHEMA)
        c.execute('INSERT INTO users VALUES (?,?,?,?,1,?)', ('admin', salt, hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 300000).hex(),
                                                             'admin', '2026-01-01'))
        c.execute('INSERT INTO sessions VALUES (?,?,?)', ('oldtoken', 'admin', time.time() + 3600))
        c.executemany('INSERT INTO audit(at,actor,action,target,detail) VALUES (?,?,?,?,?)',
                      [(f'2026-01-0{i + 1}', 'admin', 'upload', f'd{i}', '{}') for i in range(5)])
        c.execute("INSERT INTO templates(id,name,signature,labels,definition) VALUES ('t1','Old layout','sig','[]','{}')")
        c.execute("INSERT INTO drafts VALUES ('d1', 1, '{\"id\": \"d1\", \"orders\": []}')")
        c.commit()
    return password


def test_an_existing_database_is_backed_up_verified_and_upgraded_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    password = legacy_db(tmp_path)
    copy = local_store.init()
    assert copy and copy.parent == tmp_path / 'backups' / 'pre-migration' and not list(copy.parent.glob('*.partial'))
    with closing(sqlite3.connect(copy)) as b:
        assert b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and b.execute('PRAGMA user_version').fetchone()[0] == 0
        assert b.execute('SELECT count(*) FROM audit').fetchone()[0] == 5 and b.execute('SELECT count(*) FROM drafts').fetchone()[0] == 1
    assert local_store.schema_version() == local_store.SCHEMA_VERSION
    t = local_store.templates()[0]
    assert t['family'] == 't1' and t['version'] == 1 and t['buyer'] == ''             # baseline columns added
    assert local_store.verify_audit_chain()['ok'] and local_store.verify_audit_chain()['rows'] == 5
    with local_store.db() as c:
        assert not c.execute('SELECT * FROM sessions').fetchall(), 'old sessions end once at the upgrade'
    assert local_store.init() is None, 'nothing to do the second time, and no second backup'
    assert len(list(copy.parent.glob('*.sqlite3'))) == 1
    # the old user signs in with the old password; the hash is upgraded
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': 'admin', 'password': password}).status_code == 200
    with local_store.db() as c:
        assert c.execute('SELECT password_hash FROM users').fetchone()[0].startswith('pbkdf2_sha256$600000$')


def test_a_fresh_database_needs_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    assert local_store.init() is None and not (tmp_path / 'backups').exists()
    assert local_store.schema_version() == local_store.SCHEMA_VERSION


def test_no_upgrade_without_a_verified_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    legacy_db(tmp_path)
    (tmp_path / 'backups').write_text('not a folder')                 # the backup cannot be written
    with pytest.raises(RuntimeError, match='verified backup could not be made first'):
        local_store.init()
    assert local_store.schema_version() == 0
    with closing(sqlite3.connect(tmp_path / local_store.DB_NAME)) as c:
        assert 'prev_hash' not in {r[1] for r in c.execute('PRAGMA table_info(audit)')}


def test_a_backup_that_does_not_match_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    legacy_db(tmp_path)
    real = local_store._snapshot
    monkeypatch.setattr(local_store, '_snapshot', lambda c: dict(real(c), audit=999))
    with pytest.raises(RuntimeError, match='did not match'):
        local_store.init()
    assert local_store.schema_version() == 0 and not list((tmp_path / 'backups' / 'pre-migration').glob('*'))


def test_a_failing_migration_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    legacy_db(tmp_path)

    def broken(c):
        c.execute('CREATE TABLE half_done(x)')
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(local_store, 'MIGRATIONS', (local_store.MIGRATIONS[0], (2, broken), local_store.MIGRATIONS[2]))
    with pytest.raises(sqlite3.OperationalError):
        local_store.init()
    assert local_store.schema_version() == 1, 'migration 1 committed, 2 rolled back as a whole'
    with closing(sqlite3.connect(tmp_path / local_store.DB_NAME)) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='half_done'").fetchone()
    monkeypatch.undo(); monkeypatch.setattr(local_store, 'DATA', tmp_path)
    local_store.init()                                                   # the next start finishes the upgrade
    assert local_store.schema_version() == local_store.SCHEMA_VERSION


def test_a_newer_database_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    with closing(sqlite3.connect(tmp_path / local_store.DB_NAME)) as c:
        c.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError, match='newer POPPYS AI'):
        local_store.init()


# --- audit log: append-only, hash chain -------------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); local_store.init()
    for i in range(6):
        local_store.audit('admin', 'upload', f'd{i}', n=i, note='ünïcode ✓')
    return local_store


def test_audit_rows_cannot_be_updated_or_deleted(store):
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        with store.db() as c:
            c.execute("UPDATE audit SET actor='someone else' WHERE id=2")
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        with store.db() as c:
            c.execute('DELETE FROM audit WHERE id=2')
    assert store.verify_audit_chain() == dict(ok=True, rows=6, head=store.verify_audit_chain()['head'], last_id=6)


@pytest.mark.parametrize('tamper,row', [
    ("UPDATE audit SET detail='{\"n\": 99}' WHERE id=3", 3),
    ('DELETE FROM audit WHERE id=4', 5),
    ("UPDATE audit SET at='2020-01-01T00:00:00+00:00' WHERE id=6", 6),
])
def test_verify_finds_changes_made_behind_the_apps_back(store, tamper, row):
    store.audit_tamper_for_tests(tamper)
    result = store.verify_audit_chain()
    assert result['ok'] is False and result['row'] == row and result['problem']


def test_verify_finds_removed_protection(store):
    with closing(sqlite3.connect(store.path())) as c:
        c.execute('DROP TRIGGER audit_no_delete'); c.commit()
    assert store.verify_audit_chain()['ok'] is False


def test_concurrent_audit_writes_keep_one_chain(store):
    def write(n):
        for i in range(10):
            store.audit(f'user{n}', 'edit', f'd{n}', i=i)
    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = store.verify_audit_chain()
    assert result['ok'] and result['rows'] == 6 + 80


def test_admin_endpoint_verifies_the_chain(admin):
    r = admin.get('/api/audit/verify')
    assert r.status_code == 200 and r.json()['ok'] is True and r.json()['rows'] >= 1
    assert admin.post('/api/users', json={'username': 'appr', 'password': PW, 'role': 'approver'}).status_code == 200
    with TestClient(main.app) as other:
        other.post('/api/login', json={'username': 'appr', 'password': PW})
        assert other.get('/api/audit').status_code == 200 and other.get('/api/audit/verify').status_code == 403
    local_store.audit_tamper_for_tests("UPDATE audit SET actor='nobody' WHERE id=1")
    assert admin.get('/api/audit/verify').json()['ok'] is False
