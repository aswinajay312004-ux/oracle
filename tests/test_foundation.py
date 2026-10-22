"""AI-ready data foundation: identity, lineage, blank meanings, quality, catalog ID cards, readiness steps,
metrics and the future-AI context. Oracle part is opt-in (POPPYS_TEST_ORACLE=1)."""
import os
import secrets
import pytest
from fastapi.testclient import TestClient
from backend import entry, foundation, local_store, main
from tests import factory


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'})
        yield c


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_status_moves_one_checked_step_at_a_time(client):
    with local_store.db() as s:
        s.execute("INSERT INTO catalog_tables(name, status) VALUES ('T1', 'DISCOVERED')")
        s.execute("INSERT INTO catalog_columns(table_name, column_name) VALUES ('T1', 'A')")
    with pytest.raises(ValueError, match='grain'):
        foundation.document('T1', dict(status='DOCUMENTED', business_name='Thing', purpose='Things'), 'admin')
    with pytest.raises(ValueError, match='one step'):
        foundation.document('T1', dict(status='BUSINESS_APPROVED'), 'admin')
    card = foundation.document('T1', dict(status='DOCUMENTED', business_name='Thing', purpose='Things', grain='one thing'), 'admin')
    assert card['status'] == 'DOCUMENTED' and not card['ai_searchable']
    assert foundation.search('thing')[0]['table'] == 'T1'
    assert foundation.search('thing', ai_ready_only=True) == []  # undocumented/unapproved data is never offered to an AI


@pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
def test_foundation_end_to_end(client):
    ref = 'TEST-OC-' + secrets.token_hex(4).upper()
    d = ok(client.post('/api/uploads', files={'file': ('bom.xlsx', factory.merch_bom(ocn=ref))}))
    d = ok(client.post(f"/api/drafts/{d['id']}/values", json={'version': d['version'], 'order': 0,
                                                                  'table': entry.MAIN, 'values': {'PRICE': {'na': True}}}))
    p = ok(client.post(f"/api/drafts/{d['id']}/preview", json={'version': d['version']}))
    assert p['plan']['errors'] == [] and p['plan']['table'] == entry.MAIN
    r = ok(client.post(f"/api/drafts/{d['id']}/accept", json={'version': p['version'], 'plan_hash': p['plan']['hash']}))
    key = r['orders'][0]['ordmasid']

    prof = ok(client.get(f'/api/orders/{key}/foundation'))
    assert prof['order_ref'] == ref and prof['links'] == [dict(table_name=entry.MAIN, rows=3)]
    states = {f['column_name']: f['status'] for f in prof['fields']}
    assert states['BUYERNAME'] == 'PROVIDED' and states['PRICE'] == 'NOT_APPLICABLE'
    assert states['ORDERDATE'] == 'NOT_PROVIDED'
    assert {q['rule']: q['outcome'] for q in prof['quality']}['Saved values read back from Oracle'] == 'PASS'

    lin = ok(client.get(f'/api/orders/{key}/lineage', params={'column': 'BUYERNAME'}))
    assert lin['lineage'][0]['cell'] == 'C2' and lin['lineage'][0]['saved_value'] == 'TEST BUYER'
    row = ok(client.get(f'/api/orders/{key}'))['rows'][entry.MAIN][0]['_id']
    ch = ok(client.post(f'/api/orders/{key}/changes', json={'changes': [{'id': row, 'column': 'ORDERQTY', 'value': '8'}]}))
    assert ch['errors'] == []
    ok(client.post(f"/api/changes/{ch['id']}/approve", json={'hash': ch['hash']}))
    hist = ok(client.get(f'/api/orders/{key}/lineage', params={'column': 'ORDERQTY', 'row': row}))['history']
    assert [h['value'] for h in hist] == ['5', '8'] and hist[1]['previous'] == '5'  # original vs current, both kept

    sync = ok(client.post('/api/catalog/refresh'))
    assert sync['tables'] >= 1
    card = ok(client.get(f'/api/catalog/tables/{entry.MAIN}'))
    assert card['grain'].startswith('one size') and card['status'] == 'DOCUMENTED'
    assert next(c for c in card['columns'] if c['column_name'] == 'SHIPDT')['business_name'] is None
    assert any(m['name'] == 'Order quantity (pieces)' and m['status'] == 'PROPOSED' for m in ok(client.get('/api/metrics')))
    assert ok(client.get('/api/ai/context', params={'q': 'buyer quantity'}))['tables'] == []  # nothing is AI ready yet
    assert any(h['table'] == entry.MAIN for h in ok(client.get('/api/catalog/search', params={'q': entry.MAIN})))
