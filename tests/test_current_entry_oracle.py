"""Opt-in checks for the current ORDERENTRY flow against a local test Oracle."""
import os
import secrets
import pytest
from fastapi.testclient import TestClient

from backend import entry, local_store, main, oracle_store
from backend.config import ROOT, setting
from tests import factory


pytestmark = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Local Oracle opt-in only')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'qa-admin', 'password': 'test-password-123'}).status_code == 200
        yield c


def test_printing_headers_do_not_auto_select_an_erp_table(client):
    sample = (ROOT / 'samples' / 'Test_A_with_printing.xlsx').read_bytes()
    response = client.post('/api/uploads', files={'file': ('Test_A_with_printing.xlsx', sample)})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert [t['table'] for t in draft['targets']] == [entry.MAIN]
    assert draft['plan']['errors'] == []
    assert draft['plan']['totals']['main_rows'] == 12
    assert any(section['title'] == 'PRINTING' for section in draft['sections'])
    assert draft['document']['counts']['missing'] == 0


def test_test_order_approval_readback_and_retry(client):
    assert entry.MAIN == 'ORDERENTRY'
    assert setting('ORACLE_DSN').startswith(('127.0.0.1:', 'localhost:'))
    with oracle_store.connect() as conn:
        cur = conn.cursor()
        cur.execute("select sys_context('USERENV','DB_NAME'), user from dual")
        assert cur.fetchone() == ('FREEPDB1', 'POPPYS_APP')
        assert {'SELECT', 'INSERT'} <= entry.privileges(conn, entry.MAIN)

    ref = 'TEST-OC-' + secrets.token_hex(4).upper()
    response = client.post('/api/uploads', files={'file': ('test-bom.xlsx', factory.merch_bom(ocn=ref))})
    assert response.status_code == 200, response.text
    draft = response.json()
    plan = draft['plan']
    assert not plan['errors'] and plan['totals']['main_rows'] == 3
    url = f"/api/drafts/{draft['id']}/accept"
    assert client.post(url, json={'version': draft['version'] - 1, 'plan_hash': plan['hash']}).status_code == 409
    saved = client.post(url, json={'version': draft['version'], 'plan_hash': plan['hash']})
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt['verified'] and receipt['verified_rows'] == 3
    assert receipt['orders'][0]['order_ref'] == ref
    detail = client.get(f"/api/orders/{receipt['orders'][0]['ordmasid']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['summary']['missing'] == 0
    assert detail.json()['summary']['size_total'] == 21
    retry = client.post(url, json={'version': draft['version'], 'plan_hash': plan['hash']})
    assert retry.status_code == 200 and retry.json()['already_committed']
