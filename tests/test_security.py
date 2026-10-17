"""Security hardening (docs/RESEARCH_DECISIONS.md checklist items 3, 4, 39-42 and the formula-injection part of 13):
request body limits that count streamed bytes, error answers that never leak internals, request IDs, security
headers and CSP, session cookies and timeouts, CSRF, password hashing and policy, sign-in throttling and lockout,
and formula / DDE injection flags in the planner."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest
import starlette.formparsers as formparsers
from fastapi.testclient import TestClient

from backend import local_store, main, security, workbooks
from backend.erp import catalog, mapping, planner
from fixtures import merch_bom

ROOT = Path(__file__).resolve().parents[1]
PW = 'test-password-123'
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def admin(client):
    assert client.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
    return client


def sessions_of(username):
    with local_store.db() as c:
        return c.execute('SELECT * FROM sessions WHERE username=?', (username,)).fetchall()


def body_without_id(r):
    b = r.json(); b.pop('request_id', None); return b


# --- 1. request body limit --------------------------------------------------------------------------------------------------
def _asgi(scope_headers, path, chunks, method='POST'):
    """Call the app directly with a body streamed in chunks and no Content-Length (what a chunked upload looks like)."""
    sent = []; body = iter(chunks)

    async def receive():
        try:
            return {'type': 'http.request', 'body': next(body), 'more_body': True}
        except StopIteration:
            return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method, 'scheme': 'http',
             'path': path, 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
             'headers': [(b'host', b'testserver')] + scope_headers, 'client': ('127.0.0.1', 5000), 'server': ('testserver', 80),
             'state': {}}
    asyncio.run(main.app(scope, receive, send))
    start = next(m for m in sent if m['type'] == 'http.response.start')
    return start['status'], dict((k.decode().lower(), v.decode()) for k, v in start['headers']), b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')


class Recording(formparsers.SpooledTemporaryFile):
    biggest = 0

    def write(self, data):
        n = super().write(data)
        Recording.biggest = max(Recording.biggest, self.tell())
        return n


def test_chunked_30_mib_upload_without_content_length_is_refused_and_the_temp_file_stays_under_the_limit(admin, monkeypatch):
    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', Recording); Recording.biggest = 0
    boundary = 'poppysboundary'
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.xlsx"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode()
    chunk = b'x' * 65536
    chunks = [head] + [chunk] * (30 * 16)                          # 30 MiB of file data, never a Content-Length
    status, headers, body = _asgi([(b'content-type', f'multipart/form-data; boundary={boundary}'.encode())], '/api/uploads', chunks)
    assert status == 413
    assert 'larger than 14 MB' in json.loads(body)['detail']
    assert 0 < Recording.biggest <= security.UPLOAD_LIMIT, Recording.biggest
    assert headers['x-request-id'] and headers['content-security-policy'] == CSP


def test_declared_content_length_over_the_limit_is_refused_before_reading(admin):
    r = admin.post('/api/uploads', content=b'x' * 10, headers={'Content-Length': str(15 * 1024 * 1024), 'Content-Type': 'application/octet-stream'})
    assert r.status_code == 413 and 'larger than 14 MB' in r.json()['detail'] and r.json()['request_id']


def test_other_routes_have_a_1_mib_limit_streamed_or_declared(client):
    big = json.dumps({'username': 'admin', 'password': 'x' * (1024 * 1024 + 10)}).encode()
    r = client.post('/api/login', content=big, headers={'Content-Type': 'application/json'})
    assert r.status_code == 413 and 'larger than 1024 KB' in r.json()['detail']
    status, _, body = _asgi([(b'content-type', b'application/json')], '/api/login', [big[i:i + 65536] for i in range(0, len(big), 65536)])
    assert status == 413 and 'larger than 1024 KB' in json.loads(body)['detail']
    # just under the limit is read normally (and fails validation, not the size check)
    r = client.post('/api/login', json={'username': 'admin', 'password': 'x' * 300})
    assert r.status_code == 422


def test_saving_a_large_draft_edit_has_the_upload_limit_not_1_mib(admin):
    d = admin.post('/api/uploads', files={'file': ('bom.xlsx', merch_bom())}).json()
    orders = deepcopy(d['orders']); orders[0]['description'] = 'x' * (2 * 1024 * 1024)      # a 2 MiB edit
    r = admin.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': orders})
    assert r.status_code != 413, r.text
    r = admin.put(f'/api/drafts/{d["id"]}', content=b'x', headers={'Content-Length': str(15 * 1024 * 1024), 'Content-Type': 'application/json'})
    assert r.status_code == 413 and 'changes are larger than 14 MB' in r.json()['detail']
    assert security.body_limit({'method': 'POST', 'path': '/api/drafts/abc/map'}) == security.DEFAULT_LIMIT


def test_an_upload_under_the_limit_still_works(admin):
    r = admin.post('/api/uploads', files={'file': ('bom.xlsx', merch_bom())})
    assert r.status_code == 200, r.text


# --- 2. error answers ----------------------------------------------------------------------------------------------------------
def raising_in_backend(exc):
    """A function whose frame lives in backend/ (as if our own code raised exc)."""
    code = compile('def boom(*a, **k):\n    raise EXC\n', str(ROOT / 'backend' / 'pretend_module.py'), 'exec')
    ns = {'EXC': exc}; exec(code, ns)    # noqa: S102  test helper: builds a frame that looks like backend code
    return ns['boom']


def test_our_value_error_keeps_its_message_but_never_a_path_or_secret(admin, monkeypatch):
    monkeypatch.setenv('POPPYS_ORACLE_PASSWORD', 'S3cret-Value-42')
    monkeypatch.setattr(local_store, 'summaries', raising_in_backend(ValueError(
        'Could not read C:\\x\\secret\\book.xlsx or /home/user/oracle/data/x.upload with S3cret-Value-42. Fix the file.')))
    r = admin.get('/api/drafts')
    assert r.status_code == 409
    text = r.text
    assert 'C:\\' not in text and 'secret\\book' not in text and '/home/user' not in text and 'S3cret-Value-42' not in text
    assert 'Fix the file.' in r.json()['detail'] and r.json()['request_id'] == r.headers['x-request-id']


def test_library_value_error_is_a_500_with_a_reference_and_the_traceback_only_in_the_log(admin, monkeypatch, tmp_path):
    def broken():
        return json.loads('{"C:\\\\x\\\\secret": ')      # json raises JSONDecodeError (a ValueError) from its own frame
    monkeypatch.setattr(local_store, 'summaries', broken)
    r = admin.get('/api/drafts')
    assert r.status_code == 500 and r.headers['content-type'].startswith('application/problem+json')
    rid = r.json()['request_id']
    assert r.json()['detail'] == f'Something went wrong. Nothing was saved. Reference {rid}.'
    assert 'Expecting' not in r.text and 'secret' not in r.text and 'Traceback' not in r.text
    main.logs.flush()
    lines = [json.loads(x) for x in (tmp_path / 'logs' / 'app.jsonl').read_text(encoding='utf-8').splitlines()]
    err = [x for x in lines if x.get('event') == 'unhandled_error' and x.get('request_id') == rid]
    assert err and 'JSONDecodeError' in err[0]['traceback'] and err[0]['level'] == 'ERROR'


def test_unexpected_exception_is_a_problem_response_with_headers(admin, monkeypatch):
    monkeypatch.setattr(local_store, 'summaries', lambda: 1 / 0)
    r = admin.get('/api/drafts', headers={'X-Request-ID': 'support-case-0042'})
    assert r.status_code == 500 and r.json()['request_id'] == 'support-case-0042' == r.headers['x-request-id']
    assert 'ZeroDivision' not in r.text and r.headers['content-security-policy'] == CSP


def test_runtime_error_from_a_library_is_not_shown(admin, monkeypatch):
    def broken():
        import threading
        threading.Thread().join()        # RuntimeError raised inside the threading module
    monkeypatch.setattr(local_store, 'summaries', broken)
    r = admin.get('/api/drafts')
    assert r.status_code == 500 and 'thread' not in r.text.lower()


def test_validation_errors_do_not_echo_the_input_or_context(client):
    r = client.post('/api/login', json={'username': 'bad name!', 'password': 'P' * 300})
    assert r.status_code == 422
    for e in r.json()['detail']:
        assert set(e) <= {'type', 'loc', 'msg'}, e
    assert 'P' * 50 not in r.text and 'bad name!' not in r.text


def test_api_docs_are_off_in_production(client):
    assert security.production()
    for path in ('/openapi.json', '/docs', '/redoc'):
        assert client.get(path).status_code == 404


def test_api_docs_are_on_only_when_development_is_chosen(tmp_path):
    code = 'import backend.main as m; print(m.app.openapi_url, m.app.docs_url)'
    env = {'PATH': '', 'POPPYS_ENV': 'development', 'POPPYS_DATA_DIR': str(tmp_path)}
    env.update({k: os.environ[k] for k in ('SYSTEMROOT', 'WINDIR') if k in os.environ})   # Windows sockets need these
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    assert out.stdout.split() == ['/openapi.json', '/docs'], out.stderr[-2000:]


# --- 3. request ID -------------------------------------------------------------------------------------------------------------
def test_request_id_is_made_or_a_safe_one_is_kept(client):
    r = client.get('/api/session')
    assert re.fullmatch(r'[0-9a-f]{32}', r.headers['x-request-id'])
    assert client.get('/api/session', headers={'X-Request-ID': 'ps-upgrade-1234'}).headers['x-request-id'] == 'ps-upgrade-1234'
    for bad in ('short', 'has space inside it', 'x' * 65, 'abc\u00e9defghij', '../../etc/passwd'):
        got = client.get('/api/session', headers={'X-Request-ID': bad.encode('utf-8')}).headers['x-request-id']
        assert got != bad and re.fullmatch(r'[0-9a-f]{32}', got)


# --- 8. headers --------------------------------------------------------------------------------------------------------------------
def test_every_response_has_the_security_headers(admin):
    responses = [admin.get('/'), admin.get('/static/styles.css'), admin.get('/api/session'), admin.get('/api/nothing-here'),
                 admin.post('/api/uploads', headers={'Content-Length': str(20 * 1024 * 1024)}, content=b''),
                 admin.post('/api/logout', headers={'Sec-Fetch-Site': 'cross-site'}), admin.get('/healthz'),
                 TestClient(main.app).get('/api/drafts'), admin.get('/', headers={'Host': 'evil.example'})]
    for r in responses:
        h = r.headers
        assert h['content-security-policy'] == CSP and 'unsafe-inline' not in h['content-security-policy'], r.url
        assert h['x-content-type-options'] == 'nosniff' and h['x-frame-options'] == 'DENY' and h['referrer-policy'] == 'no-referrer'
        assert h['cross-origin-opener-policy'] == 'same-origin' and h['cross-origin-resource-policy'] == 'same-origin'
        assert all(f'{p}=()' in h['permissions-policy'] for p in ('camera', 'microphone', 'geolocation', 'payment', 'usb'))
        assert 'server' not in h and h['x-request-id']
    statuses = [r.status_code for r in responses]
    assert statuses == [200, 200, 200, 404, 413, 403, 200, 401, 400]
    for r in responses[2:5] + responses[6:8]:
        assert r.headers['cache-control'] == 'no-store', r.url
    assert responses[0].headers['cache-control'] == 'no-cache'


def test_launchers_hide_the_server_header():
    assert '--no-server-header' in (ROOT / 'start.ps1').read_text()
    assert '--no-server-header' in (ROOT / 'deploy' / 'windows' / 'PoppysAI.xml').read_text()


# --- 5. sessions ----------------------------------------------------------------------------------------------------------------------
def test_session_cookie_flags_in_production(client, monkeypatch):
    monkeypatch.setenv('POPPYS_COOKIE_SECURE', 'true')
    r = client.post('/api/setup', json={'username': 'admin', 'password': PW})
    cookie = r.headers['set-cookie']
    assert cookie.startswith('poppys_session=')
    for flag in ('HttpOnly', 'Secure', 'Path=/', 'SameSite=strict', 'Max-Age=28800'):
        assert flag.lower() in cookie.lower(), cookie
    r = client.post('/api/logout', headers={'Cookie': cookie.split(';')[0]})
    gone = r.headers['set-cookie'].lower()
    assert 'max-age=0' in gone and 'secure' in gone and 'httponly' in gone and 'path=/' in gone and 'samesite=strict' in gone
    assert r.headers['clear-site-data'] == '"cache", "cookies", "storage"'


def test_secure_flag_can_be_turned_off_for_tests_only(admin):
    assert not security.cookie_secure()   # tests/conftest.py
    assert 'secure' not in admin.post('/api/login', json={'username': 'admin', 'password': PW}).headers['set-cookie'].lower()


def test_idle_timeout_and_rate_limited_last_seen(admin, monkeypatch):
    monkeypatch.setattr(main.oracle, 'connect', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Oracle is off in this test.')))
    monkeypatch.setattr(main.ai, 'status', lambda: {'available': False})
    assert len(sessions_of('admin')) == 1
    with local_store.db() as c:
        c.execute('UPDATE sessions SET last_seen=?', (time.time() - 30,))
    assert admin.get('/api/drafts').status_code == 200
    assert abs(sessions_of('admin')[0]['last_seen'] - (time.time() - 30)) < 2, 'not written again within 60 s'
    with local_store.db() as c:
        c.execute('UPDATE sessions SET last_seen=?', (time.time() - 61,))
    assert admin.get('/api/drafts').status_code == 200
    assert time.time() - sessions_of('admin')[0]['last_seen'] < 5, 'refreshed after 60 s'
    # the page's own status poll is not activity
    with local_store.db() as c:
        c.execute('UPDATE sessions SET last_seen=?', (time.time() - 120,))
    assert admin.get('/api/health').status_code == 200 and admin.get('/api/session').json()['authenticated']
    assert time.time() - sessions_of('admin')[0]['last_seen'] >= 119
    with local_store.db() as c:
        c.execute('UPDATE sessions SET last_seen=?', (time.time() - 31 * 60,))
    assert admin.get('/api/drafts').status_code == 401
    assert not sessions_of('admin'), 'the expired session is deleted'


def test_absolute_timeout(admin):
    with local_store.db() as c:
        c.execute('UPDATE sessions SET created=?, expires=?', (time.time() - 8 * 3600 - 1, time.time() - 1))
    assert admin.get('/api/drafts').status_code == 401


def test_sign_in_never_reuses_the_presented_session(admin):
    first = admin.cookies.get('poppys_session')
    assert admin.post('/api/login', json={'username': 'admin', 'password': PW}).status_code == 200
    second = admin.cookies.get('poppys_session')
    assert first != second
    hashes = {r['token_hash'] for r in sessions_of('admin')}
    assert security.token_hash(first) not in hashes and security.token_hash(second) in hashes


@pytest.mark.parametrize('change', [{'role': 'viewer'}, {'active': False}, {'password': 'another-pass-phrase'}])
def test_a_change_of_role_active_or_password_ends_that_users_sessions(admin, change):
    assert admin.post('/api/users', json={'username': 'opuser', 'password': PW, 'role': 'operator'}).status_code == 200
    with TestClient(main.app) as op:
        assert op.post('/api/login', json={'username': 'opuser', 'password': PW}).status_code == 200
        assert op.get('/api/drafts').status_code == 200
        r = admin.patch('/api/users/opuser', json=change)
        assert r.status_code == 200 and r.json()['sessions_ended'] == 1
        assert op.get('/api/drafts').status_code == 401
    assert admin.get('/api/drafts').status_code == 200


def test_admin_can_sign_out_everyone_else(admin):
    assert admin.post('/api/users', json={'username': 'opuser', 'password': PW, 'role': 'operator'}).status_code == 200
    clients = [TestClient(main.app) for _ in range(2)]
    for c in clients:
        assert c.post('/api/login', json={'username': 'opuser', 'password': PW}).status_code == 200
    r = admin.post('/api/sessions/sign-out-everyone')
    assert r.status_code == 200 and r.json()['sessions_ended'] == 2
    assert all(c.get('/api/drafts').status_code == 401 for c in clients)
    assert admin.get('/api/drafts').status_code == 200
    assert any(e['action'] == 'sessions_revoked' for e in admin.get('/api/audit').json())
    with TestClient(main.app) as op:
        op.post('/api/login', json={'username': 'opuser', 'password': PW})
        assert op.post('/api/sessions/sign-out-everyone').status_code == 403


def test_logout_ends_the_session(admin):
    token = admin.cookies.get('poppys_session')
    r = admin.post('/api/logout')
    assert r.status_code == 200 and r.headers['clear-site-data'] == '"cache", "cookies", "storage"'
    assert not sessions_of('admin')
    assert TestClient(main.app, cookies={'poppys_session': token}).get('/api/drafts').status_code == 401


# --- 6. CSRF --------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('headers,status', [
    ({'Sec-Fetch-Site': 'cross-site'}, 403),
    ({'Sec-Fetch-Site': 'same-site'}, 403),
    ({'Sec-Fetch-Site': 'something-new'}, 403),
    ({'Origin': 'https://attacker.example'}, 403),
    ({'Origin': 'http://testserver:3000'}, 403),
    ({'Origin': 'null'}, 403),
    ({'Referer': 'http://localhost:3000/page'}, 403),
    ({'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://evil.example'}, 403),
    ({'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://testserver'}, 200),
    ({'Sec-Fetch-Site': 'none'}, 200),
    ({'Origin': 'http://testserver'}, 200),
    ({'Referer': 'http://testserver/#workspace'}, 200),
    ({}, 200),                                  # no browser: PowerShell, curl, scripts
])
def test_csrf_rules_for_state_changing_requests(admin, headers, status):
    r = admin.put('/api/settings', json={'default_sample_type': 'DEVELOPMENT SAMPLE'}, headers=headers)
    assert r.status_code == status, (headers, r.text)
    if status == 403:
        assert r.json()['code'] == 'csrf' and 'Cross-origin' in r.json()['detail']


def test_csrf_does_not_block_reads(admin):
    assert admin.get('/api/drafts', headers={'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://attacker.example'}).status_code == 200


def test_csrf_blocks_a_cross_site_login_and_upload(client):
    assert client.post('/api/setup', json={'username': 'admin', 'password': PW}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post('/api/uploads', files={'file': ('a.xlsx', b'x')}, headers={'Origin': 'http://localhost:9999'}).status_code == 403


# --- 7. passwords ---------------------------------------------------------------------------------------------------------------------
def test_new_hashes_are_pbkdf2_600k_with_the_count_stored(admin):
    with local_store.db() as c:
        row = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    scheme, iterations, salt, digest = row['password_hash'].split('$')
    assert scheme == 'pbkdf2_sha256' and iterations == '600000' and salt == row['salt'] and len(digest) == 64
    assert hashlib.pbkdf2_hmac('sha256', PW.encode(), bytes.fromhex(salt), 600000).hex() == digest


def test_legacy_300k_hash_still_signs_in_and_is_upgraded(admin):
    salt = secrets.token_hex(16); legacy_pw = 'old10chars'          # created before the 12-character rule
    legacy = hashlib.pbkdf2_hmac('sha256', legacy_pw.encode(), bytes.fromhex(salt), 300000).hex()
    with local_store.db() as c:
        c.execute("INSERT INTO users(username,salt,password_hash,role,active,created_at) VALUES ('veteran',?,?,'operator',1,'x')", (salt, legacy))
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': 'veteran', 'password': 'wrong-password'}).status_code == 401
        assert c.post('/api/login', json={'username': 'veteran', 'password': legacy_pw}).status_code == 200
    with local_store.db() as c:
        stored = c.execute("SELECT password_hash FROM users WHERE username='veteran'").fetchone()[0]
    assert stored.startswith('pbkdf2_sha256$600000$')
    assert security.verify_password(legacy_pw, stored) == (True, False)
    assert any(e['action'] == 'password_rehashed' for e in admin.get('/api/audit').json())
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': 'veteran', 'password': legacy_pw}).status_code == 200


@pytest.mark.parametrize('password,why', [
    ('short-pass1', 'at least 12'),
    ('password1234', 'too common'),
    ('Password@2026', 'too common'),
    ('qwertyuiop123', 'too common'),
    ('aaaaaaaaaaaaaaaa', 'too common'),
    ('my-opuser-password!', 'username'),
    ('x' * 257, 'at most 256'),
])
def test_new_passwords_follow_the_policy_everywhere(admin, password, why):
    r = admin.post('/api/users', json={'username': 'opuser', 'password': password, 'role': 'operator'})
    assert r.status_code == 422 and why in json.dumps(r.json()), r.text
    if len(password) <= 256:
        assert password not in r.text
    assert admin.post('/api/users', json={'username': 'opuser', 'password': 'blue tractor seventeen', 'role': 'operator'}).status_code == 200
    r = admin.patch('/api/users/opuser', json={'password': password})
    assert r.status_code == 422, r.text


def test_setup_refuses_a_weak_first_password(client):
    assert client.post('/api/setup', json={'username': 'admin', 'password': 'admin1234567'}).status_code == 422
    assert client.post('/api/setup', json={'username': 'boss', 'password': 'Boss-2026-loom'}).status_code == 422   # holds the username
    assert client.post('/api/setup', json={'username': 'boss', 'password': 'loom and needle 77'}).status_code == 200


# --- 7. throttling and lockout ----------------------------------------------------------------------------------------------------------
def fail(c, username='admin', password='not-the-password'):
    return c.post('/api/login', json={'username': username, 'password': password})


def test_unknown_user_and_wrong_password_look_the_same(admin):
    a, b = fail(admin, 'admin'), fail(admin, 'nobody-here')
    assert a.status_code == b.status_code == 401 and body_without_id(a) == body_without_id(b) == {'detail': 'Username or password is incorrect.'}
    assert set(a.headers) - {'x-request-id', 'content-length'} == set(b.headers) - {'x-request-id', 'content-length'}


def test_after_five_failures_the_next_try_waits_and_is_refused_without_checking(admin):
    for _ in range(5):
        assert fail(admin).status_code == 401
    with local_store.db() as c:            # as if the five came just now (hashing takes a while on a slow machine)
        c.execute('UPDATE auth_failures SET at=?', (time.time(),))
    r = admin.post('/api/login', json={'username': 'admin', 'password': PW})      # even the right password
    assert r.status_code == 429 and r.headers['retry-after'] == '1' and 'Wait 1 second' in r.json()['detail']
    with local_store.db() as c:            # time passes: the wait is over
        c.execute('UPDATE auth_failures SET at=at-2')
    assert admin.post('/api/login', json={'username': 'admin', 'password': PW}).status_code == 200


def test_the_wait_grows_and_survives_a_restart(admin):
    with local_store.db() as c:
        c.executemany('INSERT INTO auth_failures(username,ip,at) VALUES (?,?,?)', [('admin', '10.9.9.9', time.time() - 9 + i) for i in range(9)])
    with TestClient(main.app) as fresh:                                            # lifespan again: a restarted server
        r = fail(fresh)
        assert r.status_code == 429 and 10 <= int(r.headers['retry-after']) <= 16, r.headers   # 9 failures: 2^4 = 16 s
    assert security.wait_for(9, 1000.0, 1000.0) == 16 and security.wait_for(30, 1000.0, 1000.0) == 60
    assert security.wait_for(4, 1000.0, 1000.0) == 0 and security.wait_for(6, 1000.0, 1003.0) == 0


def test_failures_from_one_address_across_many_usernames_are_throttled(admin):
    for i in range(5):
        assert fail(admin, f'guess{i}').status_code == 401
    with local_store.db() as c:
        c.execute('UPDATE auth_failures SET at=?', (time.time(),))
    assert fail(admin, 'guess-new').status_code == 429


def test_twenty_failures_in_fifteen_minutes_lock_the_username_and_an_admin_can_unlock(admin):
    # 19 earlier failures spread over the last 14 minutes, the newest long enough ago that no wait applies
    with local_store.db() as c:
        c.executemany('INSERT INTO auth_failures(username,ip,at) VALUES (?,?,?)',
                      [('admin', f'10.0.0.{i}', time.time() - 840 + i * 40) for i in range(19)])
        c.execute('UPDATE auth_failures SET at=? WHERE id=(SELECT max(id) FROM auth_failures)', (time.time() - 61,))
    assert fail(admin).status_code == 401                                           # the 20th
    r = admin.post('/api/login', json={'username': 'admin', 'password': PW})
    assert r.status_code == 429 and 'locked' in r.json()['detail'] and 13 * 60 <= int(r.headers['retry-after']) <= 15 * 60
    events = [e['action'] for e in admin.get('/api/audit').json()]
    assert 'login_locked' in events
    assert admin.get('/api/users').json()[0]['locked_until']
    assert admin.post('/api/users/admin/unlock').json() == {'ok': True, 'was_locked': True}
    assert admin.post('/api/login', json={'username': 'admin', 'password': PW}).status_code == 200
    assert 'user_unlocked' in [e['action'] for e in admin.get('/api/audit').json()]


def test_a_lock_on_an_unknown_username_looks_the_same(admin):
    with local_store.db() as c:
        for name in ('admin', 'ghost'):
            c.execute('INSERT INTO auth_locks(username,until,at,failures) VALUES (?,?,?,20)', (name, time.time() + 900, 'x'))
    a, b = fail(admin, 'admin'), fail(admin, 'ghost')
    assert a.status_code == b.status_code == 429 and body_without_id(a) == body_without_id(b)


# --- version ---------------------------------------------------------------------------------------------------------------------------
def test_version_needs_sign_in_and_reports_versions(admin):
    assert TestClient(main.app).get('/api/version').status_code == 401
    v = admin.get('/api/version').json()
    assert v['version'] == (ROOT / 'VERSION').read_text().strip()
    assert v['python'].count('.') == 2 and v['sqlite'] and v['oracledb'] and v['schema'] == local_store.SCHEMA_VERSION
    assert v['git_sha'] is None or re.fullmatch(r'[0-9a-f]{40}', v['git_sha'])


def test_git_sha_comes_from_the_release_file_or_the_checkout(tmp_path):
    assert security.git_sha(tmp_path) is None
    (tmp_path / '.git' / 'refs' / 'heads').mkdir(parents=True)
    (tmp_path / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
    (tmp_path / '.git' / 'packed-refs').write_text('# pack-refs\n' + 'a' * 40 + ' refs/heads/main\n')
    assert security.git_sha(tmp_path) == 'a' * 40
    (tmp_path / '.git' / 'refs' / 'heads' / 'main').write_text('b' * 40 + '\n')
    assert security.git_sha(tmp_path) == 'b' * 40
    (tmp_path / 'GIT_SHA').write_text('c' * 40)
    assert security.git_sha(tmp_path) == 'c' * 40


# --- 11. formula / DDE injection -----------------------------------------------------------------------------------------------------
def order(**changes):
    o = workbooks.parse(merch_bom())['orders'][0]
    o.update(order_ref='TEST/1', order_date='2026-10-17', quantity_basis='order_demo', lines=deepcopy(o['order_lines']))
    o.update(qty_unit='PCS', pieces_per_unit=1)
    o.update(changes)
    return o


def plan(o):
    return planner.plan(catalog.SnapshotCatalog(), [o], 'f' * 64, mapping.effective())


@pytest.mark.parametrize('value,risk', [
    ('=SUM(A1:A9)', 'warning'), ('+CHART', 'warning'), ('- NAVY', 'warning'), ('@SUM(1)', 'warning'), ('\t=1', 'warning'),
    ('\r=1', 'warning'), ('＝1+1', 'warning'), ('＠x', 'warning'), ('-', None), ('---', None), ('-5', None), ('+91.5', None),
    ('-1,200.50', None), ('NAVY', None), ('PK/26-27/S/9001', None), ('HIDDEN', None), ('DDE', None), ('ADDED', None),
    ("=cmd|' /C calc'!A0", 'error'), ('@cmd|x!A0', 'error'), ("msexcel|'\\\\..\\\\x'!a", 'error'), ('=HYPERLINK("http://x","y")', 'error'),
    ('-WEBSERVICE("http://x")', 'error'), ('=IMPORTXML(A1,"//a")', 'error'), ('=DDE("cmd";"/c calc";"a")', 'error'),
    ('{DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe}', 'error'), ('note =hyperlink("x")', 'error'),
])
def test_formula_risk(value, risk):
    assert planner.formula_risk(value) == risk


def test_planner_flags_formulas_on_the_field_and_never_evaluates_them():
    p = plan(order(po_number='=1+2', style='T1001'))
    hits = [i for i in p['issues'] if 'formula' in i['message']]
    assert hits and all(i['severity'] == 'warning' for i in hits) and 'orders.0.po_number' in {i['path'] for i in hits}
    ordmas = next(op for op in p['operations'] if op['table'] == 'ORDMAS')
    assert '=1+2' in ordmas['values'].values(), 'kept exactly as typed: text, never calculated'
    assert not [e for e in p['errors'] if 'formula' in e or 'spreadsheet' in e]


def test_planner_refuses_dde_payloads():
    p = plan(order(po_number="=cmd|' /C calc'!A0"))
    errs = [i for i in p['issues'] if i['severity'] == 'error' and 'spreadsheet command' in i['message']]
    assert errs and errs[0]['path'] == 'orders.0.po_number' and 'Nothing was run' in errs[0]['message']


def test_new_master_values_are_flagged_on_their_cell():
    o = order(); o['lines'][0]['color'] = '@SUM(1)'
    p = plan(o)
    hit = [i for i in p['issues'] if 'formula' in i['message'] and 'COLORMAS' in i['message']]
    assert hit and hit[0]['path'] == 'orders.0.lines.0.color'
