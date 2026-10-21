"""Opt-in end-to-end workflow against the local Oracle demo, through the HTTP API.
Writes clearly labelled TEST orders only. Schema-change tests need POPPYS_TEST_DDL=1
because they add (never drop) a TEST table in GARMENT_DEMO."""
import os
import secrets
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from tests import factory

pytestmark = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'})
        yield c


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, content, name='Buyer_Order.xlsx'):
    return ok(client.post('/api/uploads', files={'file': (name, content)}))


def test_upload_map_preview_approve_edit_and_audit(client):
    ref = 'TEST-' + secrets.token_hex(4).upper()
    d = upload(client, factory.label_layout(order_no=ref))
    assert d['orders'][0]['order_ref'] == ref
    # Drag Excel cell E3 (season) onto ORDMAS.REMARKS; the server reads the value itself.
    d = ok(client.post(f'/api/drafts/{d["id"]}/bind', json=dict(version=d['version'], order=0, target='column:ORDMAS.REMARKS', sheet='Buyer Order', cell='E3')))
    assert d['orders'][0]['extras']['REMARKS']['value'] == 'SS27'
    p = ok(client.post(f'/api/drafts/{d["id"]}/preview', json={'version': d['version']}))
    plan = p['plan']
    assert plan['errors'] == [], plan['errors']
    head = next(op for op in plan['operations'] if op['table'] == 'ORDMAS')
    assert head['values']['REMARKS'] == 'SS27' and head['values']['OQTY'] == 5000
    assert 'ODATE' not in head['values']  # optional and absent: NULL, not an error
    # Stale approval is refused.
    assert client.post(f'/api/drafts/{d["id"]}/accept', json={'version': p['version'] - 1, 'plan_hash': plan['hash']}).status_code == 409
    receipt = ok(client.post(f'/api/drafts/{d["id"]}/accept', json={'version': p['version'], 'plan_hash': plan['hash']}))
    assert receipt['verified'] and receipt['verified_rows'] == len(receipt['rows'])
    oid = receipt['orders'][0]['ordmasid']
    # Retry after an interrupted response returns the same receipt, never a second insert.
    assert ok(client.post(f'/api/drafts/{d["id"]}/accept', json={'version': p['version'], 'plan_hash': plan['hash']}))['already_committed']

    listed = next(o for o in ok(client.get('/api/orders')) if o['ordmasid'] == oid)
    assert (listed['order_ref'], listed['buyer'], listed['quantity']) == (ref, 'ABC TEXTILES', 5000)
    detail = ok(client.get(f'/api/orders/{oid}'))
    s = detail['summary']
    assert {x['size']: x['quantity'] for x in s['sizes']} == {'M': 2000, 'L': 3000} and s['reconciled']
    size_rows = detail['rows']['ORDSIZE']
    m_row = next(r for r in size_rows if detail['labels']['ORDSIZE.GSIZE'][str(r['GSIZE'])] == 'M')
    assert detail['sources'][f'ORDSIZE:{m_row["ORDSIZEID"]}']['RQTY']['source']['cell'] == 'B10'
    buyers = ok(client.get(f'/api/orders/{oid}/tables/BUYMAS'))
    assert [r['BUYERNAME'] for r in buyers['rows']] == ['ABC TEXTILES']

    # Edit: preview first, nothing written; totals recalculated; then approve.
    before = ok(client.get(f'/api/orders/{oid}'))['rows']['ORDSIZE']
    change = ok(client.post(f'/api/orders/{oid}/changes', json={'changes': [
        dict(table='ORDSIZE', id=m_row['ORDSIZEID'], column='RQTY', value='2500'),
        dict(table='ORDSIZE', id=m_row['ORDSIZEID'], column='PQTY', value='2500')]}))
    assert change['errors'] == []
    changed = {(c['table'], c['column']): (c['old'], c['new']) for c in change['changes']}
    assert changed[('ORDSIZE', 'RQTY')] == (2000, 2500) and changed[('ORDMAS', 'OQTY')] == (5000, 5500)
    assert changed[('ORDSTYLE', 'ORDQTY')] == (5000, 5500)
    assert ok(client.get(f'/api/orders/{oid}'))['rows']['ORDSIZE'] == before
    # A second reviewer change to the same value makes the first preview stale.
    other = ok(client.post(f'/api/orders/{oid}/changes', json={'changes': [dict(table='ORDSIZE', id=m_row['ORDSIZEID'], column='RQTY', value='2100')]}))
    ok(client.post(f'/api/changes/{other["id"]}/approve', json={'hash': other['hash']}))
    assert client.post(f'/api/changes/{change["id"]}/approve', json={'hash': change['hash']}).status_code == 409
    fresh = ok(client.post(f'/api/orders/{oid}/changes', json={'changes': [dict(table='ORDMAS', id=oid, column='BUYER', value='TEST BUYER TEXTILES'),
                                                                         dict(table='ORDMAS', id=oid, column='ODATE', value='2026-11-01')]}))
    assert ok(client.post(f'/api/changes/{fresh["id"]}/approve', json={'hash': fresh['hash']}))['applied'] == 2
    assert ok(client.post(f'/api/changes/{fresh["id"]}/approve', json={'hash': fresh['hash']}))['already_applied']
    after = ok(client.get(f'/api/orders/{oid}'))
    assert after['summary']['buyer'] == 'TEST BUYER TEXTILES' and after['summary']['order_date'] == '2026-11-01'
    history = ok(client.get(f'/api/orders/{oid}/history'))['changes']
    assert {(h['column'], h['old'], h['new']) for h in history} >= {('RQTY', '2000', '2100'), ('ODATE', None, '2026-11-01')}
    bad = ok(client.post(f'/api/orders/{oid}/changes', json={'changes': [dict(table='ORDMAS', id=oid, column='ORDMASID', value='1'),
                                                                       dict(table='ORDMAS', id=oid, column='BUYER', value='NO SUCH BUYER')]}))
    assert len(bad['errors']) == 2
    assert client.post(f'/api/changes/{bad["id"]}/approve', json={'hash': bad['hash']}).status_code == 409

    # Same order uploaded again: duplicate is reported, not inserted.
    again = upload(client, factory.label_layout(order_no=ref))
    dup = ok(client.post(f'/api/drafts/{again["id"]}/preview', json={'version': again['version']}))['plan']
    assert any('already exists' in e for e in dup['errors'])

    # Template: saved mapping reused when the layout matches, refused when it moved.
    ok(client.post('/api/templates', json={'draft_id': d['id'], 'name': 'ABC layout'}))
    reuse = upload(client, factory.label_layout(order_no=ref + 'B'))
    assert reuse['template']['score'] == 1 and reuse['orders'][0]['extras']['REMARKS']['value'] == 'SS27'
    assert upload(client, factory.moved_layout()).get('template') is None

    overview = ok(client.get('/api/overview'))
    assert overview['oracle'] and overview['saved_orders'] >= 1
    assert ok(client.get('/api/db/graph'))['relationships']


def test_unknown_layout_mapped_by_dragging_one_cell(client):
    d = upload(client, factory.horizontal_layout(), 'po.xlsx')
    o = d['orders'][0]
    d = ok(client.post(f'/api/drafts/{d["id"]}/bind', json=dict(version=d['version'], order=0, target='field:order_ref', sheet='PO', cell='B2')))
    p = ok(client.post(f'/api/drafts/{d["id"]}/preview', json={'version': d['version']}))['plan']
    assert o['buyer'] == 'DEMO BUYER'
    assert p['errors'] == [], p['errors']
    assert any('"DEMO BUYER"' in i for i in p['info']) or any(op['action'] == 'reuse' for op in p['operations'] if op['table'] == 'BUYMAS')


@pytest.mark.skipif(os.getenv('POPPYS_TEST_DDL') != '1', reason='Set POPPYS_TEST_DDL=1 to add a TEST table in GARMENT_DEMO')
def test_create_table_and_relationships(client):
    name = 'TEST_PRINT_' + secrets.token_hex(3).upper()
    spec = dict(name=name, columns=[dict(name='PRINT_ID', type='NUMBER', required=True), dict(name='ORDER_ID', type='NUMBER'),
                                    dict(name='INK_TYPE', type='VARCHAR2', length=40)], primary_key=['PRINT_ID'])
    preview = ok(client.post('/api/db/tables/preview', json=spec))
    assert preview['errors'] == [] and preview['enabled']
    assert client.post('/api/db/tables', json=dict(spec=spec, hash=preview['hash'], confirm='WRONG')).status_code == 409
    ok(client.post('/api/db/tables', json=dict(spec=spec, hash=preview['hash'], confirm=name)))
    detail = ok(client.get(f'/api/db/tables/{name}'))
    assert [c['name'] for c in detail['columns']] == ['PRINT_ID', 'ORDER_ID', 'INK_TYPE'] and detail['total'] == 0
    rel = dict(parent='ORDMAS', parent_column='ORDMASID', child=name, child_column='ORDER_ID', kind='physical')
    checked = ok(client.post('/api/db/relationships/validate', json=rel))
    assert checked['errors'] == [] and checked['ddl'].startswith(f'ALTER TABLE "GARMENT_DEMO"."{name}" ADD CONSTRAINT')
    assert client.post('/api/db/relationships', json=dict(spec=rel, hash=checked['hash'])).status_code == 409
    ok(client.post('/api/db/relationships', json=dict(spec=rel, hash=checked['hash'], confirm=True)))
    edges = ok(client.get('/api/db/graph'))['relationships']
    assert any(e['child'] == name and e['kind'] == 'physical' for e in edges)
    wrong = ok(client.post('/api/db/relationships/validate', json=dict(rel, child_column='INK_TYPE', kind='application')))
    assert any('Data types do not match' in e for e in wrong['errors'])
    assert any(log['object'] == name for log in ok(client.get('/api/db/schema-log')))
