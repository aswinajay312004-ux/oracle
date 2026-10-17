"""Ask AI and the explorer use a separate account that Oracle itself keeps read-only."""
import pytest
from backend import oracle_store


class Conn:
    def __init__(self, privileges): self.privileges = privileges; self.closed = False
    def cursor(self): return self
    def execute(self, sql, *a, **k): self.sql = sql
    def fetchmany(self, n): return [(p,) for p in self.privileges][:n]
    def close(self): self.closed = True


@pytest.fixture
def account(monkeypatch):
    values = {'ORACLE_RO_USER': 'POPPYS_RO', 'ORACLE_RO_PASSWORD': 'secret-for-test'}
    monkeypatch.setattr(oracle_store, 'setting', lambda k, d='': values.get(k, d))
    monkeypatch.setattr(oracle_store, '_VERIFIED_READONLY', set())
    return values


def test_missing_readonly_account_says_how_to_create_it(monkeypatch):
    monkeypatch.setattr(oracle_store, 'setting', lambda k, d='': d)
    with pytest.raises(RuntimeError, match='create_readonly_user.py'):
        oracle_store.connect_readonly()


def test_account_with_write_privileges_is_refused(account, monkeypatch):
    conn = Conn(['INSERT on ORDMAS'])
    monkeypatch.setattr(oracle_store.oracledb, 'connect', lambda **k: conn)
    with pytest.raises(RuntimeError, match='not read-only.*INSERT on ORDMAS'):
        oracle_store.connect_readonly()
    assert conn.closed


def test_select_only_account_is_used_and_checked_once(account, monkeypatch):
    made = []
    monkeypatch.setattr(oracle_store.oracledb, 'connect', lambda **k: made.append(Conn([])) or made[-1])
    assert oracle_store.connect_readonly() is made[0] and 'user_tab_privs' in made[0].sql
    second = oracle_store.connect_readonly()
    assert not hasattr(second, 'sql')                # privileges are checked once per process


def test_ask_ai_reports_the_readonly_setup_step(monkeypatch):
    from backend import main
    monkeypatch.setattr(main.insight, 'uploads_db', lambda: Conn([]))
    monkeypatch.setattr(main.oracle, 'connect_readonly', lambda: (_ for _ in ()).throw(RuntimeError(oracle_store.READONLY_MISSING)))
    with main.query_sources() as sources:
        with pytest.raises(Exception, match='create_readonly_user.py'):
            main._require_db(sources)
