"""Opt-in: the question engine and explorer SQL against the local Oracle (read-only; nothing is written).

    $env:POPPYS_TEST_ORACLE='1'; .\\.venv\\Scripts\\python.exe -m pytest -q tests\\test_oracle_insight.py
"""
import os
import oracledb
import pytest
from backend import oracle_store, local_store
from backend.config import OWNER
from backend.insight import service, engine
from backend.insight.query import Dialect, compile_intent

pytestmark = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')


@pytest.fixture(scope='module')
def live():
    local_store.init()
    with oracle_store.connect() as conn:
        phys = service.harvest(conn)
        kb = service.build_kb(phys, oracle_store.current_mapping())
        ex = service.OracleExecutor(conn); d = Dialect('oracle', OWNER)
        yield kb, ex, d


def test_dictionary_harvest_sees_order_tables(live):
    kb, _, _ = live
    assert kb.live and {'ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'BUYMAS'} <= set(kb.tables)
    assert any(j['child'] == 'ORDSIZE' and j['parent'] == 'ORDSTYLE' for j in kb.joins)


def test_compiled_queries_run_read_only(live):
    kb, ex, d = live
    intents = [
        dict(base='ORDMAS', measures=[dict(agg='count', column=None)]),
        dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')], group_by=['BUYMAS.BUYERNAME']),
        dict(base='ORDSIZE', measures=[dict(agg='sum', column='ORDSIZE.RQTY')], group_by=['SIZE_MAS.SIZES']),
        dict(base='ORDMAS', filters=[dict(column='SIZE_MAS.SIZES', op='eq', value='M')], limit=5),
        dict(base='ORDMAS', measures=[dict(agg='count', column=None)], group_by=[dict(column='ORDMAS.ODATE', bucket='month')],
             filters=[dict(column='ORDMAS.ODATE', op='between', value=['2020-01-01', '2030-12-31'])]),
    ]
    for intent in intents:
        r = engine.run(intent, kb, {'db': (ex, d), 'local': (ex, d)})
        assert r['sql'].startswith('SELECT') and 'FETCH FIRST' in r['sql']


def test_read_only_transaction_blocks_writes(live):
    kb, ex, d = live
    c = ex.conn.cursor()
    c.execute('SET TRANSACTION READ ONLY')
    with pytest.raises(oracledb.DatabaseError):   # an Oracle refusal (read-only transaction or no privilege), not any error
        c.execute(f'UPDATE {OWNER}.ORDMAS SET REMARKS = REMARKS WHERE 1 = 0')
    ex.conn.rollback()
    with pytest.raises(ValueError):
        ex('DELETE FROM ORDMAS', {}, 1)


def test_value_index_and_question(live):
    kb, ex, d = live
    stats = service.refresh_values(kb, ex, d)
    assert stats['values'] > 0
    r = engine.ask('how many orders', kb, {'db': (ex, d), 'local': (ex, d)}, finder=service.finder(kb))
    assert r['rows'][0][0] >= 1
    comp = compile_intent(dict(base='ORDMAS', columns=['ORDMAS.DOCID', 'ORDMAS.BUYER']), kb, d)
    assert 'BUYMAS' in comp['sql']   # the buyer ID is shown as the buyer name
