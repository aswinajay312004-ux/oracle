"""Opt-in local Oracle integration. Uses labelled TEST records, rollbacks and one audited commit."""
from copy import deepcopy
import os
import secrets
import pytest
from backend import oracle_store as db,workbooks

pytestmark=pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE')!='1',reason='Set POPPYS_TEST_ORACLE=1 for existing local Oracle tests')


@pytest.fixture(autouse=True)
def shipped_mapping(tmp_path,monkeypatch):
    """The tests check the shipped write mapping, not the Setup → Write mapping choices kept in this computer's data folder."""
    from backend import local_store
    monkeypatch.setattr(local_store,'DATA',tmp_path)

def fixture_order():
    o=workbooks.base_order()
    o.update(buyer='DEMO BUYER TEXTILES',style='DEMO-ST-001',description='TEST SAMPLE BODYSUIT',
        po_number='TEST-PO',order_ref='TEST-'+secrets.token_hex(5).upper(),order_date='2026-10-17',
        ship_date='2026-10-24',qty_unit='PCS',pieces_per_unit=1,quantity_basis='sample',sample_type='DEVELOPMENT SAMPLE',
        lines=[dict(label='Test sample',color='TEST BLUE',declared_total=10,
                    sizes=[dict(size='M',quantity=4,source={}),dict(size='L',quantity=6,source={})])])
    return o

def draft():return dict(id=secrets.token_hex(16),filehash=secrets.token_hex(32),orders=[fixture_order()])

def test_preview_validation_never_inserts():
    d=draft()
    with db.connect() as c:
        cur=c.cursor();cur.execute('select count(*) from GARMENT_DEMO.ORDMAS');before=cur.fetchone()[0]
        p=db.plan(c,d['orders'],d['filehash'])
        assert not p['errors'],p['errors']
        assert p['totals']['pieces']==10
        cur.execute('select count(*) from GARMENT_DEMO.ORDMAS');assert cur.fetchone()[0]==before
        d['orders'][0]['lines'][0]['sizes'][0]['quantity']=-1
        assert db.plan(c,d['orders'],d['filehash'])['errors']

def test_mid_transaction_failure_rolls_back_all_rows(monkeypatch):
    d=draft()
    with db.connect() as c:p=db.plan(c,d['orders'],d['filehash'])
    original=db.plan
    def poison(*args,**kwargs):
        value=original(*args,**kwargs)
        value['operations'][-1]['values']['ORDSIZEID']='$missing_reference'
        value['hash']=p['hash']
        return value
    monkeypatch.setattr(db,'plan',poison)
    with pytest.raises(KeyError):db.commit(d,p['hash'],'integration-test')
    with db.connect() as c:
        cur=c.cursor();cur.execute('select count(*) from GARMENT_DEMO.ORDMAS where DOCID=:r',r=d['orders'][0]['order_ref'])
        assert cur.fetchone()[0]==0
        cur.execute('select count(*) from PP_ORDER_KEYS where JOBID=:id',id=d['id']);assert cur.fetchone()[0]==0
        assert db.read_result(cur,d['id']) is None

def test_changed_preview_is_rejected():
    d=draft()
    with db.connect() as c:p=db.plan(c,d['orders'],d['filehash'])
    d['orders'][0]['description']='ANOTHER TEST DESCRIPTION'
    with pytest.raises(ValueError,match='preview changed'):db.commit(d,p['hash'],'integration-test')

def test_commit_readback_retry_duplicate_and_quantity_reconciliation():
    d=draft()
    with db.connect() as c:p=db.plan(c,d['orders'],d['filehash'])
    receipt=db.commit(d,p['hash'],'integration-test')
    assert not receipt['already_committed']
    assert sum(r['values']['RQTY'] for r in receipt['rows'] if r['table']=='ORDSIZE')==10
    assert db.commit(d,p['hash'],'integration-test')['already_committed']
    with db.connect() as c:
        duplicate=deepcopy(d);duplicate['id']=secrets.token_hex(16)
        p2=db.plan(c,duplicate['orders'],duplicate['filehash'])
        assert any('already' in e for e in p2['errors'])
        orderid=receipt['orders'][0]['ordmasid'];cur=c.cursor()
        cur.execute('''select sum(s.RQTY) from GARMENT_DEMO.ORDSIZE s join GARMENT_DEMO.ORDSTYLE l
            on s.ORDMASID=l.ORDMASID and s.SUB3_SUBNO=l.SUBNO where l.ORDMASID=:id''',id=orderid)
        assert cur.fetchone()[0]==10


def test_live_readback_graph_matches_committed_rows():
    from backend.erp import readback
    d = draft()
    with db.connect() as c:
        p = db.plan(c, d['orders'], d['filehash'])
    receipt = db.commit(d, p['hash'], 'integration-test')
    m = db.current_mapping()
    with db.connect() as c:
        g = readback.graph(receipt, m, lambda t, pk, ids: db.live_rows(c, t, pk, ids, m))
    assert g['verified'], [n['table'] for n in g['nodes'] if not n['verified']]
    assert {'ORDMAS', 'ORDSTYLE', 'ORDSIZE'} <= {n['table'] for n in g['nodes']}
    [sheet] = g['sheets']                                   # the order as people read it, from the same fresh read
    assert sheet['verified'] and sheet['total'] == 10 and sheet['sizes'] == ['M', 'L']
    assert sheet['lines'][0]['colour'] == 'TEST BLUE' and sheet['lines'][0]['sizes'] == {'M': 4, 'L': 6}
    assert {f['label']: f['value'] for f in sheet['header']}['Buyer'] == 'DEMO BUYER TEXTILES'


@pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
def test_axpert_gap_report_compares_axpert_rows_with_poppys_rows_read_only():
    from scripts import axpert_gap_report as gap
    with db.connect() as conn:
        r = gap.report(conn, 'GARMENT_DEMO', sample=200)
    ordmas = next(t for t in r['tables'] if t['table'] == 'ORDMAS')
    assert ordmas['visible'] and ordmas['axpert_rows'] >= 1 and {c['column'] for c in ordmas['columns']} >= {'DOCID', 'BUYER', 'OQTY'}
    docid = next(c for c in ordmas['columns'] if c['column'] == 'DOCID')
    assert docid['axpert_pct'] == 100.0 and docid['status'] in ('both', 'no POPPYS rows')
    assert '| Column |' in gap.markdown(r)


def test_empty_reference_gets_the_next_oc_number_of_the_series_on_real_oracle():
    from datetime import date
    from backend.erp import order_numbers as on
    spec = on.series()
    if spec is None:
        pytest.skip('No automatic OC number series in config/company.json')
    head = on.prefix(spec, date.today())
    with db.connect() as c:
        cur = c.cursor(); cur.execute('select DOCID from GARMENT_DEMO.ORDMAS where DOCID like :p', p=head + '%')
        expected = on.following([r[0] for r in cur], head, 2, spec['digits'])
    numbers = []
    for _ in range(2):
        d = draft(); d['orders'][0]['order_ref'] = ''
        with db.connect() as c:
            p = db.plan(c, d['orders'], d['filehash'])
        assert not p['errors'], p['errors']
        receipt = db.commit(d, p['hash'], 'integration-test')
        numbers.append(receipt['orders'][0]['order_ref'])
        with db.connect() as c:
            cur = c.cursor(); cur.execute('select DOCID from GARMENT_DEMO.ORDMAS where ORDMASID=:id', id=receipt['orders'][0]['ordmasid'])
            assert cur.fetchone()[0] == numbers[-1]
    assert numbers == expected
