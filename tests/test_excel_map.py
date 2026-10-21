"""The person's own Excel → Oracle mapping (backend/erp/excel_map.py, New order → Map to Oracle): the cells an order was
read from as targets, blank fields left blank, extra ORDMAS columns, one Oracle row per Excel row, typed values."""
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from fake_oracle import FakeOracle
from fixtures import merch_bom, SIZES

PW = 'test-password-123'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    oracle = FakeOracle(masters={'BUYMAS': [dict(id=7, label='TESTBUYER', cancelled=False)],
                                 'UNITMAS': [dict(id=8, label='PCS', cancelled=False)]}).install(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        yield c, oracle


def upload(c):
    r = c.post('/api/uploads', files={'file': ('bom.xlsx', merch_bom())}); assert r.status_code == 200, r.text
    return r.json()


def test_targets_start_from_the_cells_the_order_was_read_from(env):
    c, _ = env
    d = upload(c)
    o = d['orders'][0]
    # nothing filled in for fields the file leaves blank (owner, 2026-10-21)
    assert o['order_date'] == '' and o['exporter'] == '' and o['sample_type'] == ''
    m = c.get(f'/api/drafts/{d["id"]}/oracle-map').json()
    assert m['automatic'] is True and m['sheet'] == 'D99001'
    t = m['targets']
    assert t['ORDMAS.BUYER'] == ['C2'] and t['ORDMAS.STYLENO'] == ['C4'] and t['ORDMAS.STYLEDESC'] == ['C5']
    assert t['ORDSTYLE.COMBO'] == ['C12', 'C13']
    assert t['ORDSIZE.GSIZE'] == ['E11', 'F11', 'G11', 'H11']
    assert len(t['ORDSIZE.RQTY']) == 8 and 'E12' in t['ORDSIZE.RQTY']
    tables = {x['table']: {col['name']: col for col in x['columns']} for x in m['tables']}
    assert set(tables) >= {'ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'ORDACC'}
    assert tables['ORDMAS']['ORDMASID']['kind'] == 'auto' and tables['ORDMAS']['BUYER']['kind'] == 'field'
    assert tables['ORDMAS']['REMARKS']['kind'] == 'extra' and tables['ORDSIZE']['RQTY']['kind'] == 'qty'
    assert tables['ORDACC']['ACCDESC']['kind'] == 'row' and tables['ORDACC']['ORDACCROW']['kind'] == 'auto'
    assert tables['ORDSTYLE']['STNO']['kind'] == 'auto'          # a copy of the order's style


def test_mapped_cells_become_the_rows_oracle_receives(env):
    c, oracle = env
    d = upload(c)
    t = c.get(f'/api/drafts/{d["id"]}/oracle-map').json()['targets']
    t.update({'ORDMAS.FINHDATE': ['E4'], 'ORDMAS.REMARKS': ['E6'],
              'ORDACC.ACCDESC': ['D32', 'D33', 'D34', 'D35'], 'ORDACC.APOQTY': ['H32', 'H33', 'H34', 'H35']})
    t.pop('ORDMAS.STYLEDESC')                                       # not mapped → stays blank
    r = c.post(f'/api/drafts/{d["id"]}/oracle-map', json={'version': d['version'], 'sheet': 'D99001', 'targets': t,
                                                           'typed': {'ORDMAS.BONO': 'PO-9'}})
    assert r.status_code == 200, r.text
    d = r.json(); o = d['orders'][0]
    assert o['description'] == '' and o['po_number'] == 'PO-9' and o['buyer'] == 'TESTBUYER'
    assert [l['color'] for l in o['lines']] == ['WHITE AOP', 'PINK SOLID']
    assert o['oracle_columns']['ORDMAS']['FINHDATE']['value'] == '2026-11-01'
    assert [x['ACCDESC']['value'] for x in o['oracle_rows']['ORDACC']] == ['0-3 Mths', '3-6 Mths', '6-12 Mths', '12-18 Mths']
    # the screen shows the person's mapping from now on
    m = c.get(f'/api/drafts/{d["id"]}/oracle-map').json()
    assert m['automatic'] is False and m['typed'] == {'ORDMAS.BONO': 'PO-9'} and m['targets']['ORDMAS.REMARKS'] == ['E6']
    o['qty_unit'] = 'PCS'; o['pieces_per_unit'] = 1
    e = c.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': [o]}); assert e.status_code == 200, e.text
    p = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': e.json()['version']}).json()
    assert not p['plan']['errors'], p['plan']['errors']
    ops = p['plan']['operations']
    head = next(x for x in ops if x['table'] == 'ORDMAS')['values']
    assert head['FINHDATE'] == '2026-11-01' and head['REMARKS'] == 'SEA' and head['BONO'] == 'PO-9'
    assert 'STYLEDESC' not in head and head.get('ODATE') is None and head.get('SAMPLETYPE') in ('', None)   # NULL in Oracle
    acc = [x['values'] for x in ops if x['table'] == 'ORDACC']
    assert [a['ACCDESC'] for a in acc] == ['0-3 Mths', '3-6 Mths', '6-12 Mths', '12-18 Mths']
    assert [a['ORDACCROW'] for a in acc] == [1, 2, 3, 4] and [a['APOQTY'] for a in acc] == [15, 30, 45, 60]
    assert all(a['ORDMASID'] == '$o1' for a in acc)
    assert not any(n.get('reference_only') and n['table'] == 'ORDACC' for n in p['plan']['graph']['nodes'])
    ex = (p['plan'].get('exceptions') or {}).get('digest')
    r = c.post(f'/api/drafts/{d["id"]}/accept', json={'version': p['version'], 'plan_hash': p['plan']['hash'], 'exceptions_seen': ex})
    assert r.status_code == 200, r.text
    assert sum(1 for row in r.json()['rows'] if row['table'] == 'ORDACC') == 4
    assert c.get(f'/api/history/{d["id"]}/graph').json()['verified'] is True


def test_blank_quantity_cells_add_no_size_rows_and_wrong_targets_are_refused(env):
    c, _ = env
    d = upload(c)
    base = {'ORDSTYLE.COMBO': ['C12'], 'ORDSIZE.GSIZE': ['E11', 'F11', 'G11', 'H11', 'I11'], 'ORDSIZE.RQTY': ['E12', 'F12', 'J12']}
    r = c.post(f'/api/drafts/{d["id"]}/oracle-map', json={'version': d['version'], 'sheet': 'D99001', 'targets': base})
    assert r.status_code == 200, r.text
    [line] = r.json()['orders'][0]['lines']
    assert [(s['size'], s['source']['cell']) for s in line['sizes']] == [(SIZES[0], 'E12'), (SIZES[1], 'F12')]   # J12 is blank: no row
    v = r.json()['version']
    for bad in ({'ORDMAS.ORDMASID': ['C2']}, {'ORDMAS.NOPE': ['C2']}, {'BUYMAS.BUYERNAME': ['C2']}, {'ORDMAS.BUYER': ['ZZZZ1']}):
        assert c.post(f'/api/drafts/{d["id"]}/oracle-map', json={'version': v, 'sheet': 'D99001', 'targets': bad}).status_code == 422


def test_one_access_level_everyone_can_approve_their_own_upload(env):
    c, _ = env
    s = c.get('/api/session').json()
    assert {'upload', 'approve', 'users', 'settings'} <= set(s['permissions'])
    assert c.post('/api/users', json={'username': 'team', 'password': 'stitch-rate-plan-7', 'role': 'viewer'}).status_code == 200
    c.post('/api/logout'); assert c.post('/api/login', json={'username': 'team', 'password': 'stitch-rate-plan-7'}).status_code == 200
    assert 'approve' in c.get('/api/session').json()['permissions']
