"""Single-table mode (GARMENT_DEMO.EXCEL_ORDERS): label matching rules without Oracle, and the full
upload -> preview -> approve -> verify -> edit flow against the local Oracle demo (opt-in)."""
import os
import secrets
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main, order_table, workbooks
from backend.workbooks import norm
from tests import factory


def cols():
    out = {}
    for name, kind, req, level, field, labels in order_table.SPEC:
        base = kind.split('(')[0]
        size = int(kind.split('(')[1].split()[0].split(',')[0].rstrip(')')) if '(' in kind else None
        out[name] = dict(type=base, length=size or 22, precision=size if base == 'NUMBER' else None, scale=0 if base == 'NUMBER' else None,
                         nullable='N' if req else 'Y', default=None, label=labels[0], synonyms={norm(x) for x in labels} | {norm(name.replace('_', ' '))}, level=level)
    return out


def test_header_labels_match_columns_and_header_rows_read_downwards():
    data = workbooks.parse(factory.merch_bom())
    found = order_table.label_values(data['sheets'], cols())
    assert found['BUYERNAME']['source']['cell'] == 'C2' and found['IONO']['source']['cell'] == 'B9'
    assert found['SHIPDT']['value'] == '2027-02-18'
    assert 'PACKQTY' not in found  # label present, value cell empty: stays blank, nothing invented


def test_plan_rows_blanks_and_reviewer_choices():
    data = workbooks.parse(factory.merch_bom())
    o = data['orders'][0]
    plan = order_table.build_plan(cols(), [o], data['sheets'], 'bom.xlsx')
    assert plan['errors'] == [] and len(plan['operations']) == 3  # one row per size with a quantity
    row = plan['operations'][0]['values']
    assert (row['IONO'], row['SIZES'], row['ORDERQTY'], row['QTYTYPE']) == ('TEST-OC-1', 'S', 5, 'ORDER')
    assert 'ORDERDATE' not in row and 'PRICE' not in row
    o['table_values'] = {'SEASON': dict(value=None, source={'origin': 'Left blank by you'}), 'REMARKS': dict(value='Rush', source={})}
    again = order_table.build_plan(cols(), [o], data['sheets'], 'bom.xlsx')['operations'][0]['values']
    assert 'SEASON' not in again and again['REMARKS'] == 'Rush'
    dup = order_table.build_plan(cols(), [o], data['sheets'], 'bom.xlsx', existing_refs={'TEST-OC-1'})
    assert any('already saved' in e for e in dup['errors'])


@pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
def test_single_table_upload_preview_approve_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setenv('POPPYS_ORDER_TARGET', 'table')
    ref = 'TEST-OC-' + secrets.token_hex(4).upper()
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'})
        d = c.post('/api/uploads', files={'file': ('bom.xlsx', factory.merch_bom(ocn=ref))}).json()
        d = c.post(f"/api/drafts/{d['id']}/bind", json=dict(version=d['version'], order=0, target=f'column:{order_table.TABLE}.REMARKS', sheet='ST1', cell='C3')).json()
        p = c.post(f"/api/drafts/{d['id']}/preview", json={'version': d['version']}).json()
        plan = p['plan']
        assert plan['mode'] == 'table' and plan['errors'] == [], plan['errors']
        assert plan['operations'][0]['values']['REMARKS'] == 'SS-27'
        r = c.post(f"/api/drafts/{d['id']}/accept", json={'version': p['version'], 'plan_hash': plan['hash']}).json()
        assert r['verified'] and r['verified_rows'] == 3
        key = r['orders'][0]['ordmasid']
        detail = c.get(f'/api/orders/{key}').json()
        assert detail['mode'] == 'table' and detail['summary']['size_total'] == 21
        assert c.get(f'/api/orders/{key}/tables/{order_table.TABLE}').json()['rows'][0]['IONO'] == ref
        first = detail['rows'][order_table.TABLE][0]['ENTRY_ID']
        ch = c.post(f'/api/orders/{key}/changes', json={'changes': [{'id': first, 'column': 'ORDERQTY', 'value': '6'},
                                                                   {'id': 'all', 'column': 'ORDERDATE', 'value': '2026-11-02'}]}).json()
        assert ch['errors'] == []
        assert c.post(f"/api/changes/{ch['id']}/approve", json={'hash': ch['hash']}).json()['verified']
        after = c.get(f'/api/orders/{key}').json()['summary']
        assert after['size_total'] == 22 and after['order_date'] == '2026-11-02'
        again = c.post('/api/uploads', files={'file': ('bom.xlsx', factory.merch_bom(ocn=ref))}).json()
        dup = c.post(f"/api/drafts/{again['id']}/preview", json={'version': again['version']}).json()['plan']
        assert any('already saved' in e for e in dup['errors'])


@pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
def test_ask_ai_answers_from_fixed_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main.agent, 'available', lambda model=None: (None, []))   # keyword reader; no model in CI
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'})
        r = c.post('/api/ai/ask', json={'question': 'how many pieces for TEST BUYER?'}).json()
        assert r['intent'] == 'total_pieces' and 'TEST BUYER' in r['text'] and 'SUM(ORDERQTY)' in r['sql']
        sizes = c.post('/api/ai/ask', json={'question': 'size wise pieces'}).json()
        assert sizes['intent'] == 'by_size' and sizes['rows']
