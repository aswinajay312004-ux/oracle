"""The saved order as people read it (readback.order_sheets): header by business name and a colour × size table, built
from the fresh read-back after saving and from the plan before approval."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from backend.erp import order_numbers
from fake_oracle import FakeOracle
from fixtures import merch_bom

PW = 'test-password-123'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(order_numbers, 'series', lambda company=None: dict(format='PK/{fy}/S/{n}', year_starts_month=4, digits=0))
    oracle = FakeOracle(masters={'BUYMAS': [dict(id=7, label='TESTBUYER', cancelled=False)],
                                 'UNITMAS': [dict(id=8, label='PCS', cancelled=False)]}).install(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        assert c.put('/api/settings', json={'allow_self_approval': True}).status_code == 200
        yield c, oracle


def preview(c, sample=(3, 4, 5, 6)):
    d = c.post('/api/uploads', files={'file': ('bom.xlsx', merch_bom(sample=sample))}).json()
    o = deepcopy(d['orders'][0])
    o.update(order_ref='', order_date='2026-10-10', qty_unit='PCS', pieces_per_unit=1)
    r = c.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': [o]}); assert r.status_code == 200, r.text
    p = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': r.json()['version']}).json()
    assert not p['plan']['errors'], p['plan']['errors']
    return d['id'], p


def test_preview_shows_the_order_to_be_saved_as_a_colour_by_size_table(env):
    c, _ = env
    _, p = preview(c)
    [sheet] = p['plan']['graph']['sheets']
    assert sheet['order_ref'] == '(OC No. given on save)'          # the stand-in never reaches the screen as AUTO-OC#1#
    assert sheet['sizes'] == ['0-3M', '3-6M', '6-12M', '12-18M']
    assert [l['colour'] for l in sheet['lines']] == ['WHITE AOP', 'PINK SOLID']
    assert sheet['lines'][0]['sizes'] == {'0-3M': 3, '3-6M': 4, '6-12M': 5, '12-18M': 6} and sheet['lines'][0]['total'] == 18
    assert sheet['size_totals'] == {'0-3M': 6, '3-6M': 8, '6-12M': 10, '12-18M': 12}
    assert sheet['total'] == p['plan']['totals']['pieces'] == 36
    labels = {f['label']: f['value'] for f in sheet['header']}
    assert labels['Buyer'] == 'TESTBUYER' and labels['Order confirmation date'] == '2026-10-10'


def test_saved_order_sheet_comes_from_the_read_back_with_names_not_ids(env):
    c, oracle = env
    draft_id, p = preview(c)
    r = c.post(f'/api/drafts/{draft_id}/accept', json={'version': p['version'], 'plan_hash': p['plan']['hash'],
                                                    'exceptions_seen': (p['plan'].get('exceptions') or {}).get('digest')})
    assert r.status_code == 200, r.text
    g = c.get(f'/api/history/{draft_id}/graph').json()
    [sheet] = g['sheets']
    ref = r.json()['orders'][0]['order_ref']
    assert sheet['order_ref'] == ref and sheet['verified'] and sheet['total'] == 36
    labels = {f['label']: f['value'] for f in sheet['header']}
    assert labels['Buyer'] == 'TESTBUYER' and labels['Order unit'] == 'PCS'      # masters decoded, not record IDs
    assert all(not f['column'].endswith('ORDMASID') for f in sheet['header'])
    # a size changed in Oracle after the save shows in the table and marks the order as different
    size = next(k for k, v in oracle.rows['ORDSIZE'].items() if v['RQTY'] == 6)
    oracle.rows['ORDSIZE'][size]['RQTY'] = 60
    [changed] = c.get(f'/api/history/{draft_id}/graph').json()['sheets']
    assert not changed['verified'] and changed['total'] == 90


def test_preview_says_before_the_click_when_a_second_approver_is_needed(env):
    c, _ = env
    _, p = preview(c)
    assert p['approval'] == {'needs_other_approver': False}           # self-approval is on in this fixture
    assert c.put('/api/settings', json={'allow_self_approval': False}).status_code == 200
    draft_id, p = preview(c, sample=(4, 4, 5, 6))
    assert p['approval'] == {'needs_other_approver': True}
    r = c.post(f'/api/drafts/{draft_id}/accept', json={'version': p['version'], 'plan_hash': p['plan']['hash'],
                                                    'exceptions_seen': (p['plan'].get('exceptions') or {}).get('digest')})
    assert r.status_code == 403 and 'four-eyes' in r.json()['detail']
