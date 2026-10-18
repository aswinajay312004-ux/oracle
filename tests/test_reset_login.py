"""scripts/reset_login.py: removes every sign-in account after typed confirmation, keeps all other local data."""
import importlib.util
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reset(tmp_path, monkeypatch):
    for mod in (local_store, main):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    local_store.init()
    spec = importlib.util.spec_from_file_location('reset_login', ROOT / 'scripts' / 'reset_login.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_reset_removes_accounts_and_sessions_and_the_first_run_screen_returns(reset):
    client = TestClient(main.app)
    assert client.post('/api/setup', json=dict(username='old.name@gmail.com', password='old-password-1')).status_code == 200
    with local_store.db() as c:
        c.execute("INSERT INTO settings(key, value) VALUES ('kept', '1')")
    assert reset.main([], ask=lambda _: 'DELETE') == 0
    with local_store.db() as c:
        assert not c.execute('SELECT 1 FROM users').fetchone() and not c.execute('SELECT 1 FROM sessions').fetchone()
        assert c.execute("SELECT value FROM settings WHERE key='kept'").fetchone()[0] == '1'
    assert client.get('/api/session').json() == dict(authenticated=False, needs_setup=True, username=None, role=None, permissions=[])
    event = local_store.audit_events(1)[0]
    assert event['action'] == 'login_reset' and event['detail']['removed'] == ['old.name@gmail.com']
    # the new administrator is created on the usual first-run screen; the old password no longer works
    assert client.post('/api/setup', json=dict(username='new.name@gmail.com', password='new-password-2')).json()['role'] == 'admin'
    assert client.post('/api/login', json=dict(username='old.name@gmail.com', password='old-password-1')).status_code == 401


def test_nothing_changes_without_the_typed_confirmation(reset):
    TestClient(main.app).post('/api/setup', json=dict(username='admin.user', password='a-long-password'))
    assert reset.main([], ask=lambda _: 'yes') == 1
    with local_store.db() as c:
        assert c.execute('SELECT username FROM users').fetchone()[0] == 'admin.user'
