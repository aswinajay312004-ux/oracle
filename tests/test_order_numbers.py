"""Automatic OC numbers (config/company.json → order_number): the series, the stand-in in the plan, and the number
given on save after the highest one already in ORDMAS."""
from copy import deepcopy
from datetime import date
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from backend.erp import order_numbers as on, planner, mapping
from fake_oracle import FakeOracle
from fixtures import merch_bom

PW = 'test-password-123'
SPEC = dict(format='PK/{fy}/S/{n}', year_starts_month=4, digits=0)


def test_financial_year_runs_april_to_march():
    assert on.financial_year(date(2026, 10, 18)) == '26-27'
    assert on.financial_year(date(2027, 3, 31)) == '26-27'
    assert on.financial_year(date(2027, 4, 1)) == '27-28'
    assert on.financial_year(date(2099, 12, 1)) == '99-00'
    assert on.prefix(SPEC, date(2026, 10, 18)) == 'PK/26-27/S/'


def test_next_number_follows_the_highest_of_the_same_series_only():
    existing = ['PK/26-27/S/1320', 'pk/26-27/s/1319', 'PK/25-26/S/9999', 'PK/26-27/B/5000', 'PK/26-27/S/12A', 'PK/26-27/S/', None]
    assert on.following(existing, 'PK/26-27/S/', 2) == ['PK/26-27/S/1321', 'PK/26-27/S/1322']
    assert on.following([], 'PK/27-28/S/', 1) == ['PK/27-28/S/1']                      # a new year starts at 1
    assert on.following(['PK/26-27/S/0041'], 'PK/26-27/S/', 1, digits=4) == ['PK/26-27/S/0042']


def test_only_orders_without_a_reference_get_a_number():
    orders = [dict(order_ref=''), dict(order_ref='TYPED/1'), dict(order_ref='  ')]
    got = on.assign(orders, ['PK/26-27/S/1320'], SPEC, date(2026, 10, 18))
    assert got == {on.marker(0): 'PK/26-27/S/1321', on.marker(2): 'PK/26-27/S/1322'}
    assert on.fill('AUTO-OC#3#', got) == 'PK/26-27/S/1322' and on.fill('OC AUTO-OC#1# / x', got) == 'OC PK/26-27/S/1321 / x'
    assert on.assign([dict(order_ref='X')], [], None, date(2026, 10, 18)) == {}
    with pytest.raises(ValueError):
        on.assign([dict(order_ref='')], [], None, date(2026, 10, 18))


@pytest.mark.parametrize('fmt', ['PK/{fy}/S/', 'PK/{n}/{fy}', 'PK%/{fy}/{n}', 'PK_{fy}/{n}', 'A' * 30 + '{n}'])
def test_a_malformed_series_is_refused_not_guessed(fmt):
    with pytest.raises(ValueError):
        on.series({'order_number': {'format': fmt}})
    assert on.series({}) is None and on.series({'order_number': {}}) is None


def test_plan_carries_a_stand_in_so_the_preview_hash_does_not_depend_on_the_number(monkeypatch):
    from test_planner import order_from_fixture, snapshot
    monkeypatch.setattr(on, 'series', lambda company=None: dict(SPEC))
    o = order_from_fixture(accessories=[]); o['order_ref'] = ''
    a = planner.plan(snapshot(), [o], 'f' * 64, mapping.effective())
    assert not any('reference' in e for e in a['errors']), a['errors']
    head = next(op for op in a['operations'] if op['table'] == 'ORDMAS')
    assert head['values']['DOCID'] == 'AUTO-OC#1#'
    assert any('OC number is given automatically' in w for w in a['warnings'])
    b = planner.plan(snapshot(existing_refs={'PK/26-27/S/1320'}), [deepcopy(o)], 'f' * 64, mapping.effective())
    assert a['hash'] == b['hash']
    typed = dict(deepcopy(o), order_ref='AUTO-OC#1#')
    assert any('reserved for automatic' in e for e in planner.plan(snapshot(), [typed], 'f' * 64, mapping.effective())['errors'])
    monkeypatch.setattr(on, 'series', lambda company=None: None)        # no series: a person must type it, as before
    assert any('enter a unique sample order reference' in e for e in planner.plan(snapshot(), [deepcopy(o)], 'f' * 64, mapping.effective())['errors'])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(on, 'series', lambda company=None: dict(SPEC))
    oracle = FakeOracle(masters={'BUYMAS': [dict(id=7, label='TESTBUYER', cancelled=False)],
                                 'UNITMAS': [dict(id=8, label='PCS', cancelled=False)]}).install(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        assert c.put('/api/settings', json={'allow_self_approval': True}).status_code == 200
        yield c, oracle


def save_without_reference(c, content):
    d = c.post('/api/uploads', files={'file': ('bom.xlsx', content)}).json()
    o = deepcopy(d['orders'][0])
    o.update(order_ref='', order_date='2026-10-17', quantity_basis='order_demo', lines=deepcopy(o['order_lines']),
             qty_unit='PCS', pieces_per_unit=1)
    o['accessories'] = [a for a in o['accessories'] if len(a['description']) <= 50]
    r = c.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': [o]}); assert r.status_code == 200, r.text
    p = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': r.json()['version']}).json()
    assert not p['plan']['errors'], p['plan']['errors']
    r = c.post(f'/api/drafts/{d["id"]}/accept', json={'version': p['version'], 'plan_hash': p['plan']['hash'],
                                                     'exceptions_seen': (p['plan'].get('exceptions') or {}).get('digest')})
    assert r.status_code == 200, r.text
    return d['id'], r.json()


def test_each_save_takes_the_next_number_after_the_highest_in_oracle(env):
    c, oracle = env
    head = on.prefix(SPEC, date.today())
    oracle.refs.add(f'{head}1320')                      # e.g. the last order entered in Axpert
    assert next(f for f in c.get('/api/fields').json() if f['key'] == 'order_ref')['automatic'] == head + 'N'
    first_id, first = save_without_reference(c, merch_bom())
    assert first['orders'][0]['order_ref'] == f'{head}1321' and first['orders'][0]['automatic']
    ordmas = next(r for r in first['rows'] if r['table'] == 'ORDMAS')
    assert ordmas['values']['DOCID'] == f'{head}1321'
    assert c.get(f'/api/drafts/{first_id}').json()['orders'][0]['order_ref'] == f'{head}1321'   # the draft keeps the number
    assert c.get(f'/api/history/{first_id}/graph').json()['verified'] is True
    _, second = save_without_reference(c, merch_bom(sample=(1, 2, 3, 4)))
    assert second['orders'][0]['order_ref'] == f'{head}1322'


def test_without_a_series_the_reference_field_stays_required(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(on, 'series', lambda company=None: None)
    f = next(f for f in c.get('/api/fields').json() if f['key'] == 'order_ref')
    assert f['automatic'] is None and f['required']
