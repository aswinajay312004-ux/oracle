"""Sign-in security (backend/auth.py): password hashing and policy, wrong-password throttling kept in SQLite,
session idle timeout and sign-out on role changes, and the cross-site check on changes."""
import hashlib
import secrets
import pytest
from fastapi.testclient import TestClient
from backend import auth, local_store, main

PW = 'test-password-123'


@pytest.fixture
def client(tmp_path, monkeypatch):
    for mod in (local_store, main):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    local_store.init()
    c = TestClient(main.app)
    assert c.post('/api/setup', json=dict(username='admin.user', password=PW)).status_code == 200
    return c


class Clock:
    def __init__(self, monkeypatch):
        import time
        self.now = time.time()
        monkeypatch.setattr(auth.time, 'time', lambda: self.now)
        monkeypatch.setattr(main.time, 'time', lambda: self.now)

    def wait(self, seconds):
        self.now += seconds


def login(c, username='admin.user', password=PW):
    return c.post('/api/login', json=dict(username=username, password=password))


def test_the_product_hashes_at_the_owasp_scrypt_setting():
    assert auth.COST == dict(n=2 ** 10, r=8, p=1)          # the test speed-up (tests/conftest.py) is active here …
    import importlib
    fresh = importlib.reload(importlib.import_module('backend.auth'))
    assert fresh.COST['n'] >= 2 ** 17 and fresh.COST['r'] >= 8 and fresh.COST['p'] >= 1   # … the product's own setting is OWASP's


def test_passwords_are_stored_as_salted_scrypt_and_checked_in_constant_time():
    a, b = auth.hash_password(PW), auth.hash_password(PW)
    assert a.startswith('scrypt$10$8$1$') and a != b and PW not in a
    assert auth.verify(PW, a) == (True, False)
    assert auth.verify('wrong-password-1', a) == (False, False)
    assert auth.verify(PW, 'scrypt$x$y') == (False, False) and auth.verify(PW, '') == (False, False)
    assert auth.verify_unknown(PW) is False


def test_an_account_from_an_earlier_version_signs_in_once_and_is_rehashed(client):
    salt = secrets.token_hex(16)
    old = hashlib.pbkdf2_hmac('sha256', b'old-password-1', bytes.fromhex(salt), 300000).hex()
    with local_store.db() as c:
        c.execute("INSERT INTO users(username,salt,password_hash,role,active,created_at) VALUES ('legacy',?,?,'operator',1,'x')", (salt, old))
    assert login(client, 'legacy', 'wrong-password-1').status_code == 401
    assert login(client, 'legacy', 'old-password-1').status_code == 200    # 14 characters: earlier accounts still sign in
    with local_store.db() as c:
        row = c.execute("SELECT salt, password_hash FROM users WHERE username='legacy'").fetchone()
    assert row['password_hash'].startswith('scrypt$') and row['salt'] == ''
    assert login(client, 'legacy', 'old-password-1').status_code == 200
    assert any(e['action'] == 'login' and e['detail'].get('rehashed') for e in local_store.audit_events())


@pytest.mark.parametrize('password,why', [('short-pass1', 'at least 12'), ('qwerty123456', 'too easy'), ('Password2024!', 'too easy'),
                                          ('Garments@2026', 'too easy'), ('aaaaaaaaaaaa', 'too easy'), ('new.person-secret-9', 'username')])
def test_weak_new_passwords_are_refused_with_the_reason(client, password, why):
    r = client.post('/api/users', json=dict(username='new.person', password=password, role='operator'))
    assert r.status_code == 422 and why in r.json()['detail']
    assert client.post('/api/users', json=dict(username='new.person', password='stitch-rate-plan-7', role='operator')).status_code == 200
    r = client.patch('/api/users/new.person', json=dict(password=password))
    assert r.status_code == 422 and why in r.json()['detail']


def test_setup_refuses_a_weak_first_password(tmp_path, monkeypatch):
    for mod in (local_store, main):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    local_store.init()
    c = TestClient(main.app)
    assert c.post('/api/setup', json=dict(username='boss', password='poppys123456')).status_code == 422
    assert c.get('/api/session').json()['needs_setup']


def test_wrong_passwords_slow_down_that_account_and_survive_a_restart(client, monkeypatch):
    clock = Clock(monkeypatch)
    for _ in range(auth.FREE_TRIES):
        assert login(client, password='wrong-password-1').status_code == 401
    r = login(client)                                                      # even the right password waits now
    assert r.status_code == 429 and r.headers['Retry-After'] == '1' and 'Wait 1 second,' in r.json()['detail']
    clock.wait(1.1)
    assert login(client, password='wrong-password-1').status_code == 401  # 6th wrong: the wait doubles
    assert login(client).status_code == 429
    main.store.init()                                                      # a restart keeps the count (it is in SQLite)
    assert login(client).json()['detail'] == 'Too many wrong passwords. Wait 2 seconds, then try again.'
    clock.wait(2.1)
    assert login(client).status_code == 200                                # a correct password clears the count
    assert login(client, password='wrong-password-1').status_code == 401 and login(client).status_code == 200
    actions = [e['action'] for e in local_store.audit_events()]
    assert actions.count('login_failed') == 7 and 'login_throttled' in actions


def test_unknown_names_are_throttled_the_same_way_so_refusals_reveal_nothing(client, monkeypatch):
    Clock(monkeypatch)
    for _ in range(auth.FREE_TRIES):
        assert login(client, 'nobody.here', 'wrong-password-1').json()['detail'] == 'Username or password is incorrect.'
    assert login(client, 'nobody.here', 'wrong-password-1').status_code == 429
    assert login(client).status_code == 200                                # another account is not affected


def test_one_address_trying_many_names_is_paused(client, monkeypatch):
    clock = Clock(monkeypatch)
    for i in range(auth.ADDRESS_LIMIT):
        assert login(client, f'guess{i}', 'wrong-password-1').status_code == 401
    assert login(client).status_code == 429
    clock.wait(auth.WINDOW_S + 1)
    assert login(client).status_code == 200


def test_sessions_end_after_30_idle_minutes_but_not_while_used(client, monkeypatch):
    clock = Clock(monkeypatch)
    assert login(client).status_code == 200
    for _ in range(4):                                                     # used every 20 minutes: stays signed in
        clock.wait(20 * 60)
        assert client.get('/api/session').json()['authenticated']
    clock.wait(auth.IDLE_S + 1)
    assert not client.get('/api/session').json()['authenticated']
    assert login(client).status_code == 200
    clock.wait(auth.ABSOLUTE_S + 1)                                        # 8 hours is the limit even when used
    assert not client.get('/api/session').json()['authenticated']


def test_sign_in_replaces_the_presented_session_and_logout_clears_the_browser(client):
    old = client.cookies.get('poppys_session')
    assert login(client).status_code == 200 and client.cookies.get('poppys_session') != old
    stale = TestClient(main.app); stale.cookies.set('poppys_session', old)
    assert not stale.get('/api/session').json()['authenticated']
    r = client.post('/api/logout')
    assert 'storage' in r.headers['Clear-Site-Data'] and not client.get('/api/session').json()['authenticated']


def test_a_role_change_signs_that_user_out(client):
    assert client.post('/api/users', json=dict(username='op.one', password='stitch-rate-plan-7', role='operator')).status_code == 200
    op = TestClient(main.app)
    assert login(op, 'op.one', 'stitch-rate-plan-7').status_code == 200 and op.get('/api/session').json()['role'] == 'operator'
    assert client.patch('/api/users/op.one', json=dict(role='approver')).status_code == 200
    assert not op.get('/api/session').json()['authenticated']
    assert login(op, 'op.one', 'stitch-rate-plan-7').json()['role'] == 'approver'


@pytest.mark.parametrize('headers,code', [({'Sec-Fetch-Site': 'cross-site'}, 403), ({'Sec-Fetch-Site': 'same-site'}, 403),
                                          ({'Origin': 'http://127.0.0.1:9999'}, 403), ({'Sec-Fetch-Site': 'same-origin'}, 200),
                                          ({'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://testserver'}, 200)])
def test_changes_from_another_site_or_port_are_refused(client, headers, code):
    r = client.post('/api/logout', headers=headers)
    assert r.status_code == code and 'Sec-Fetch-Site' in r.headers.get('Vary', '')
