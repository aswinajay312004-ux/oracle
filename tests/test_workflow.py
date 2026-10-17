"""End-to-end API workflow against the in-memory Oracle stand-in: upload → review → preview →
approve → read-back graph, plus roles, four-eyes, duplicate protection and audit."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from fake_oracle import FakeOracle
from fixtures import merch_bom, unknown_matrix, as_xls

PW = 'test-password-123'
OWN = {}      # passwords people chose at their first sign-in (an administrator's password is temporary)


@pytest.fixture
def env(tmp_path, monkeypatch):
    OWN.clear()
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    oracle = FakeOracle(masters={'BUYMAS': [dict(id=7, label='TESTBUYER', cancelled=False)],
                                 'UNITMAS': [dict(id=8, label='PCS', cancelled=False)]}).install(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        yield c, oracle


def login(c, user):
    c.post('/api/logout'); assert c.post('/api/login', json={'username': user, 'password': OWN.get(user, PW)}).status_code == 200
    if c.get('/api/session').json()['password_change_required']:
        assert c.get('/api/drafts').status_code == 403                   # nothing else until the person chooses a password
        r = c.post('/api/me/password', json={'current': PW, 'new': 'quiet tractor nine field'})
        assert r.status_code == 200, r.text
        OWN[user] = 'quiet tractor nine field'
        assert not c.get('/api/session').json()['password_change_required']


def approval(p, version=None, ack=True):
    return {'version': version or p['version'], 'plan_hash': p['plan']['hash'], 'acknowledged_warnings': ack}


def upload(c, content=None, name='bom.xlsx'):
    r = c.post('/api/uploads', files={'file': (name, content or merch_bom())}); assert r.status_code == 200, r.text
    return r.json()


def ready(c, d, ref='TEST/ORD/1'):
    o = deepcopy(d['orders'][0])
    o.update(order_ref=ref, order_date='2026-10-17', quantity_basis='order_demo', lines=deepcopy(o['order_lines']))
    o.update(qty_unit='PCS', pieces_per_unit=1)   # §5: the fixture BOM does not prove pieces, so the reviewer answers
    o['accessories'] = [a for a in o['accessories'] if len(a['description']) <= 50]
    r = c.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': [o]}); assert r.status_code == 200, r.text
    r = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': r.json()['version']}); assert r.status_code == 200, r.text
    return r.json()


def test_full_order_flow_with_readback_graph(env):
    c, oracle = env
    d = upload(c)
    assert d['orders'][0]['exporter'] == 'POPPYS KNITWEAR PRIVATE LIMITED'
    p = ready(c, d)
    assert p['plan']['live'] and not p['plan']['errors'], p['plan']['errors']
    r = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p))
    assert r.status_code == 200, r.text
    receipt = r.json(); assert receipt['orders'][0]['order_ref'] == 'TEST/ORD/1'
    assert {row['table'] for row in receipt['rows']} >= {'ORDMAS', 'ORDSTYLE', 'ORDSIZE'}
    assert any(x['table'] == 'BUYMAS' and x['pk'] == 7 for x in receipt['reused'])
    g = c.get(f'/api/history/{d["id"]}/graph').json()
    assert g['verified'] is True
    tables = {n['table']: n for n in g['nodes']}
    assert tables['ORDMAS']['kind'] == 'header' and tables['BUYMAS']['kind'] == 'master'
    assert any(e['source'] == 'ORDSTYLE' and e['target'] == 'ORDSIZE' for e in g['edges'])
    line = tables['ORDSTYLE']['records'][0]
    assert line['decoded']['COMBO'] in ('WHITE AOP', 'PINK SOLID')
    # retrying the same approval returns the stored receipt, never a second insert
    again = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).json()
    assert again['already_committed'] is True
    assert c.put(f'/api/drafts/{d["id"]}', json={'version': p['version'] + 1, 'orders': []}).status_code == 409
    # same file again is blocked at preview
    p2 = ready(c, upload(c), ref='TEST/ORD/2')
    assert any('already been saved to Oracle' in e for e in p2['plan']['errors'])
    assert [h['job_id'] for h in c.get('/api/history').json()] == [d['id']]
    actions = [a['action'] for a in c.get('/api/audit').json()]
    assert {'upload', 'edit', 'preview', 'approved_saved'} <= set(actions)
    edit = next(a for a in c.get('/api/audit').json() if a['action'] == 'edit' and a['target'] == d['id'])
    assert any(ch['field'].endswith('order_ref') and ch['after'] == 'TEST/ORD/1' for ch in edit['detail']['changes'])


def test_stale_or_changed_preview_cannot_be_approved(env):
    c, _ = env
    d = upload(c); p = ready(c, d)
    o = deepcopy(p['orders'][0]); o['description'] = 'CHANGED'
    e = c.put(f'/api/drafts/{d["id"]}', json={'version': p['version'], 'orders': [o]}).json()
    r = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p, e['version']))
    assert r.status_code == 409


def test_roles_and_four_eyes(env):
    c, _ = env
    for user, role in (('op', 'operator'), ('appr', 'approver'), ('look', 'viewer')):
        assert c.post('/api/users', json={'username': user, 'password': PW, 'role': role}).status_code == 200
    assert c.put('/api/settings', json={'allow_self_approval': False}).status_code == 200
    login(c, 'look')
    assert c.post('/api/uploads', files={'file': ('x.xlsx', merch_bom())}).status_code == 403
    login(c, 'op')
    d = upload(c); p = ready(c, d)
    assert c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).status_code == 403
    assert c.post('/api/mapping/status', json={'table': 'ORDFAB', 'status': 'verified'}).status_code == 403
    assert c.get('/api/users').status_code == 403
    login(c, 'appr')
    assert c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).status_code == 200
    # the uploader approving their own upload is refused while four-eyes is on
    d2 = upload(c, merch_bom(sample=(1, 1, 1, 1))); p2 = ready(c, d2, ref='TEST/ORD/9')
    assert c.post(f'/api/drafts/{d2["id"]}/accept', json=approval(p2)).status_code == 403
    login(c, 'admin')
    assert c.patch('/api/users/admin', json={'role': 'viewer'}).status_code == 409   # last admin protected


def test_four_eyes_also_refuses_an_approver_who_changed_the_draft(env):
    """Rewriting someone else's upload and approving it alone is not four eyes."""
    c, _ = env
    for user, role in (('op', 'operator'), ('appr', 'approver')):
        assert c.post('/api/users', json={'username': user, 'password': PW, 'role': role}).status_code == 200
    assert c.put('/api/settings', json={'allow_self_approval': False}).status_code == 200
    login(c, 'op'); d = upload(c)
    login(c, 'appr'); p = ready(c, d)                                    # the approver edits the operator's draft
    r = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p))
    assert r.status_code == 403 and 'uploaded or changed it' in r.json()['detail']
    login(c, 'admin'); p = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': p['version']}).json()   # a preview is not a change
    assert c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).status_code == 200


def test_warnings_must_be_acknowledged_before_approval(env):
    c, _ = env
    d = upload(c); p = ready(c, d)
    assert p['plan']['warnings']
    r = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p, ack=False))
    assert r.status_code == 409 and 'I have read these notes' in r.json()['detail']
    assert c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).status_code == 200
    saved = next(a for a in c.get('/api/audit').json() if a['action'] == 'approved_saved')
    assert saved['detail']['warnings_acknowledged'] == len(p['plan']['warnings'])


def test_bom_rows_saved_only_after_admin_verifies_mapping(env):
    c, oracle = env
    p = ready(c, upload(c))
    assert {r['table'] for r in p['plan']['reference_only']} == {'ORDFAB', 'ORDYARN', 'ORDACC'}
    m = c.get('/api/mapping').json()
    for t in m['tables']:
        if t['table'] in ('ORDFAB', 'ORDYARN', 'ORDACC'):
            assert c.post('/api/mapping/status', json={'table': t['table'], 'status': 'verified'}).status_code == 200
            for col in t['columns']:
                if not col['fixed'] and col['status'] == 'proposed' and (not col['master'] or col['master_available']):
                    assert c.post('/api/mapping/status', json={'table': t['table'], 'column': col['column'], 'status': 'verified'}).status_code == 200
    d = upload(c, merch_bom(sample=(2, 2, 2, 2))); p = ready(c, d, ref='TEST/BOM/1')
    assert not p['plan']['errors'], p['plan']['errors']
    r = c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).json()
    saved = {row['table'] for row in r['rows']}
    assert {'ORDFAB', 'ORDYARN', 'ORDACC'} <= saved
    g = c.get(f'/api/history/{d["id"]}/graph').json()
    assert g['verified'] and any(e['source'] == 'ORDFAB' and e['target'] == 'ORDYARN' for e in g['edges'])
    assert any(a['action'] == 'mapping_status' for a in c.get('/api/audit').json())


def test_mapping_new_layout_saves_template_for_next_upload(env):
    c, _ = env
    d = upload(c, unknown_matrix(), 'zen.xlsx')
    assert d['orders'] == [] and d['suggestion']['definition']['table']['type'] == 'matrix'
    r = c.post(f'/api/drafts/{d["id"]}/map', json={'version': d['version'], 'sheet': d['suggestion']['sheet'],
                                                   'definition': d['suggestion']['definition'], 'save_template': True, 'template_name': 'Zen kids PO'})
    assert r.status_code == 200, r.text
    assert r.json()['orders'][0]['buyer'] == 'ZEN KIDS'
    nxt = upload(c, unknown_matrix(), 'zen-2.xlsx')
    assert nxt['format'] == 'Template: Zen kids PO' and nxt['orders'][0]['style'] == 'ZK-55'
    assert c.get('/api/templates').json()[0]['uses'] == 1


def test_xls_upload_and_non_excel_refused(env):
    c, _ = env
    d = upload(c, as_xls(merch_bom()), 'bom.xls')
    assert d['orders'][0]['buyer'] == 'TESTBUYER' and d['format'] == 'Merch BOM'
    r = c.post('/api/uploads', files={'file': ('po.pdf', b'%PDF-1.7', 'application/pdf')})
    assert r.status_code == 415 and 'Excel' in r.json()['detail']
    r = c.post('/api/uploads', files={'file': ('renamed.xlsx', b'%PDF-1.7', 'application/octet-stream')})
    assert r.status_code == 415 and 'not an Excel workbook' in r.json()['detail']


def test_offline_preview_is_marked_and_cannot_be_approved(env, monkeypatch):
    c, _ = env
    from backend import oracle_store
    def down(*a, **k):
        raise RuntimeError('down')
    monkeypatch.setattr(oracle_store, 'connect', down)
    d = upload(c); p = ready(c, d)
    assert p['plan']['live'] is False and 'Oracle is not connected' in p['plan']['warnings'][0]
    assert c.post(f'/api/drafts/{d["id"]}/accept', json=approval(p)).status_code == 409
    assert c.get('/api/health').json()['database']['connected'] is False
