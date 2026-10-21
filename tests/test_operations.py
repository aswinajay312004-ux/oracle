"""Operations (docs/DEPLOYMENT_AND_RECOVERY.md): liveness and readiness for the Windows scripts, request IDs and JSON
logs without secrets, development-only API pages, and a local store that closes its connections and uses WAL."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend import local_store, logs, main

PW = 'test-password-123'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setenv('POPPYS_LOG_QUIET', '1')
    logs.setup(tmp_path / 'logs')
    monkeypatch.setattr(main.ai, 'available', lambda: False)
    with TestClient(main.app) as c:
        yield c, tmp_path


def test_liveness_and_readiness_need_no_sign_in_and_say_what_is_missing(client, monkeypatch):
    c, _ = client
    assert c.get('/healthz').json() == {'status': 'ok'}
    def down():
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    monkeypatch.setattr(main.oracle, 'connect', down)
    r = c.get('/readyz')
    assert r.status_code == 200 and r.json()['status'] == 'warn'
    assert {x['name']: x['status'] for x in r.json()['checks']} == {'local store': 'pass', 'oracle': 'warn', 'local ai': 'warn'}
    assert 'bootstrap' not in r.text and 'password' not in r.text.lower()


def test_readiness_fails_when_the_local_store_cannot_be_used(client, monkeypatch):
    c, _ = client
    def broken():
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(main.store, 'db', broken)
    assert c.get('/readyz').status_code == 503


def test_every_request_has_an_id_and_a_log_line_without_secrets(client):
    c, data = client
    r = c.post('/api/setup', json={'username': 'admin', 'password': PW})
    rid = r.headers['X-Request-ID']
    c.post('/api/login', json={'username': 'admin', 'password': 'Wrong-' + PW})
    lines = [json.loads(x) for x in (data / 'logs' / 'poppys.log').read_text(encoding='utf-8').splitlines()]
    mine = [x for x in lines if x.get('request') == rid]
    assert mine and mine[-1]['path'] == '/api/setup' and mine[-1]['status'] == 200 and 'ms' in mine[-1]
    text = (data / 'logs' / 'poppys.log').read_text(encoding='utf-8')
    assert PW not in text and 'poppys_session' not in text


def test_an_unexpected_error_is_logged_with_its_reference(client, monkeypatch):
    c, data = client
    c.post('/api/setup', json={'username': 'admin', 'password': PW})
    def boom(*a, **k):
        raise ZeroDivisionError('unexpected')
    monkeypatch.setattr(main.store, 'summaries', boom)
    r = c.get('/api/drafts')
    assert r.status_code == 500 and r.json()['request_id'] in r.json()['detail'] and 'Nothing was saved' in r.json()['detail']
    entry = [json.loads(x) for x in (data / 'logs' / 'poppys.log').read_text(encoding='utf-8').splitlines() if 'Unhandled' in x][-1]
    assert entry['request'] == r.json()['request_id'] and 'ZeroDivisionError' in entry['error']


def test_api_pages_are_off_unless_developing(client):
    c, _ = client
    assert c.get('/docs').status_code in (404, 200) and c.get('/openapi.json').status_code == 404


def test_the_local_store_uses_a_write_ahead_log_and_closes_its_connections(client):
    _, data = client
    with local_store.db() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')        # closed after the block


@pytest.mark.parametrize('address,ok', [('http://127.0.0.1:11434', True), ('http://localhost:11434', True), ('http://192.168.1.20:11434', True),
                                        ('http://10.0.0.5:11434', True), ('https://api.example.com', False), ('http://8.8.8.8:11434', False),
                                        ('http://ollama.example.org:11434', False)])
def test_the_local_ai_is_only_ever_called_on_this_computer_or_the_local_network(monkeypatch, address, ok):
    from backend import ai
    monkeypatch.setenv('POPPYS_OLLAMA_URL', address)
    assert ai.local_only(ai.url()) is ok
    if not ok:
        sent = []
        monkeypatch.setattr(ai.httpx, 'post', lambda *a, **k: sent.append(a))
        with pytest.raises(RuntimeError, match='nothing was sent'):
            ai._chat([{'role': 'user', 'content': 'x'}])
        assert not sent and ai.status()['available'] is False


def test_another_schema_can_be_read_but_never_written_with_a_mapping_made_for_a_different_one(monkeypatch):
    from backend import config, workbooks
    from backend.erp import catalog, mapping, planner
    o = workbooks.base_order()
    o.update(buyer='B', style='S', description='D', po_number='P', order_ref='R/1', order_date='2026-10-17', ship_date='2026-11-01',
             quantity_basis='sample', qty_unit='PCS', pieces_per_unit=1, sample_type='X',
             lines=[dict(label='S', color='RED', declared_total=None, sizes=[dict(size='M', quantity=1)])])
    assert not any('write mapping was made' in e for e in planner.plan(catalog.SnapshotCatalog(), [o], 'h' * 64, mapping.effective())['errors'])
    monkeypatch.setattr(config, 'OWNER', 'OTHER_ERP')
    errors = planner.plan(catalog.SnapshotCatalog(), [o], 'h' * 64, mapping.effective())['errors']
    assert any('made for the Oracle schema GARMENT_DEMO, but this installation reads OTHER_ERP' in e for e in errors)


def test_the_company_profile_supplies_the_starting_settings():
    from backend import config
    assert config.COMPANY['erp']['owner'] == 'GARMENT_DEMO'
    assert main.DEFAULT_SETTINGS['company_exporter'] == config.COMPANY['defaults']['company_exporter']
