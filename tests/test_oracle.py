"""Opt-in local Oracle integration. Uses labelled TEST records, rollbacks and one audited commit."""
from copy import deepcopy
import os
import secrets
import pytest
from backend import oracle_store as db,workbooks
from backend.erp import planner

pytestmark=pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE')!='1',reason='Set POPPYS_TEST_ORACLE=1 for existing local Oracle tests')

def fixture_order():
    o=workbooks.base_order()
    o.update(buyer='DEMO BUYER TEXTILES',style='DEMO-ST-001',description='TEST SAMPLE BODYSUIT',
        po_number='TEST-PO',order_ref='TEST-'+secrets.token_hex(5).upper(),order_date='2026-10-17',
        ship_date='2026-10-24',qty_unit='PCS',pieces_per_unit=1,quantity_basis='sample',
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
        value['operations'][-1]['values']['ORDSIZEID']=planner.Ref('missing_reference')
        value['hash']=p['hash']
        return value
    monkeypatch.setattr(db,'plan',poison)
    with pytest.raises(RuntimeError,match='not saved yet'):db.commit(d,p['hash'],'integration-test')
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


def test_dollar_and_nan_text_are_saved_as_typed_and_read_back_verified():
    from backend.erp import readback
    d=draft();o=d['orders'][0];o.update(po_number='$o1',description='$5 TEE '+secrets.token_hex(3).upper(),sample_type='NAN123')
    with db.connect() as c:p=db.plan(c,d['orders'],d['filehash'])
    assert not p['errors'],p['errors']
    receipt=db.commit(d,p['hash'],'integration-test');oid=receipt['orders'][0]['ordmasid']
    with db.connect() as c:
        cur=c.cursor();cur.execute('select BONO,SAMPLETYPE from GARMENT_DEMO.ORDMAS where ORDMASID=:i',i=oid)
        assert cur.fetchone()==('$o1','NAN123')
        cur.execute('select PONUM from GARMENT_DEMO.ORDSTYLE where ORDMASID=:i',i=oid);assert cur.fetchall()==[('$o1',)]
        cur.execute('select m.SDESC from GARMENT_DEMO.ORDMAS o join GARMENT_DEMO.STYLEMAS m on m.STYLEMASID=o.STYLEDESC where o.ORDMASID=:i',i=oid)
        assert cur.fetchone()[0]==o['description']
        m=db.current_mapping();g=readback.graph(receipt,m,lambda t,pk,ids:db.live_rows(c,t,pk,ids,m))
    assert g['verified'] and receipt['column_types']['ORDMAS']['BONO']=='VARCHAR2'


def test_blank_text_saves_as_null():
    from test_planner import order_from_fixture,verified_bom
    m=verified_bom();o=order_from_fixture(order_ref='TEST-'+secrets.token_hex(5).upper(),accessories=[])
    for f in o['fabrics']:f['dia']=''
    d=dict(id=secrets.token_hex(16),filehash=secrets.token_hex(32),orders=[o])
    with db.connect() as c:p=db.plan(c,d['orders'],d['filehash'],m)
    assert not p['errors'],p['errors']
    receipt=db.commit(d,p['hash'],'integration-test',mapping=m)
    assert [r['values']['DIA'] for r in receipt['rows'] if r['table']=='ORDFAB']==[None,None,None]


def test_saves_only_to_the_pinned_database(monkeypatch):
    d=draft()
    with db.connect() as c:
        p=db.plan(c,d['orders'],d['filehash']);cur=c.cursor()
        cur.execute("select sys_context('USERENV','CON_NAME') from dual");here=cur.fetchone()[0]
    monkeypatch.setenv('POPPYS_ORACLE_DB_IDENTITY','NOT_'+here)
    with pytest.raises(RuntimeError,match='Nothing was saved'):db.commit(d,p['hash'],'integration-test')
    with db.connect() as c:
        cur=c.cursor();cur.execute('select count(*) from GARMENT_DEMO.ORDMAS where DOCID=:r',r=d['orders'][0]['order_ref'])
        assert cur.fetchone()[0]==0
    monkeypatch.setenv('POPPYS_ORACLE_DB_IDENTITY',here.lower())
    assert not db.commit(d,p['hash'],'integration-test')['already_committed']


def test_main_account_cannot_lock_tables_it_only_reads():
    import oracledb
    with db.connect(purpose='privilege test') as c:
        cur=c.cursor()
        for sql in ('SELECT PARTYMASID FROM GARMENT_DEMO.PARTYMAS WHERE ROWNUM = 1 FOR UPDATE NOWAIT',
                    'LOCK TABLE GARMENT_DEMO.PARTYMAS IN EXCLUSIVE MODE NOWAIT'):
            with pytest.raises(oracledb.DatabaseError):
                cur.execute(sql);pytest.fail(f'{sql}: POPPYS_APP holds SELECT, not READ. Run python scripts/grant_bom_tables.py.')
            c.rollback()
