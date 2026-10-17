"""Independent SQL safety review (backend/insight/policy.py): every statement an executor runs is parsed and
checked separately from the compiler that built it."""
import pytest
from backend.erp import mapping as registry
from backend.insight import policy, service
from backend.insight.query import Dialect, compile_intent

OWNER = 'GARMENT_DEMO'
HOSTILE = [
    ('DELETE FROM GARMENT_DEMO.ORDMAS', 'SELECT'),
    ('UPDATE GARMENT_DEMO.ORDMAS SET ORDQTY = 0', 'SELECT'),
    ('SELECT 1 FROM GARMENT_DEMO.ORDMAS; DROP TABLE GARMENT_DEMO.ORDMAS', 'one statement'),
    ('BEGIN NULL; END;', 'could not read'),
    ('SELECT * FROM GARMENT_DEMO.ORDMAS FOR UPDATE', 'Locking'),
    ('SELECT * FROM GARMENT_DEMO.ORDMAS@prod_link', 'database links'),
    ('SELECT username FROM all_users', 'outside the ERP schema'),
    ('SELECT * FROM SYS.USER$', 'outside the ERP schema'),
    ('SELECT * FROM POPPYS_APP.PP_IMPORT_LOG', 'outside the ERP schema'),
    ('SELECT 1 FROM dual', 'outside the ERP schema'),
    ('SELECT DBMS_PIPE.RECEIVE_MESSAGE(:a) FROM GARMENT_DEMO.ORDMAS', 'not on the list'),
    ('SELECT UTL_HTTP.REQUEST(:u) FROM GARMENT_DEMO.ORDMAS', 'not on the list'),
    ('SELECT my_writer_func(ORDMASID) FROM GARMENT_DEMO.ORDMAS', 'not on the list'),
    ('SELECT LEVEL FROM GARMENT_DEMO.ORDMAS CONNECT BY LEVEL < 1000000', 'CONNECT BY'),
    ('SELECT * FROM GARMENT_DEMO.ORDMAS WHERE ORDMASID IN (SELECT ORDMASID FROM POPPYS_APP.PP_ORDER_KEYS)', 'outside the ERP schema'),
    ('WITH x AS (SELECT * FROM all_tables) SELECT * FROM x', 'outside the ERP schema'),
]


@pytest.mark.parametrize('sql,why', HOSTILE)
def test_hostile_statements_are_refused_before_reaching_oracle(sql, why):
    with pytest.raises(policy.UnsafeSQL, match=why):
        policy.check(sql, 'oracle', OWNER)


def test_compiled_oracle_answers_pass_and_name_their_tables():
    kb = service.build_kb(service.snapshot(), registry.effective())
    d = Dialect('oracle', OWNER)
    intents = [
        dict(base='ORDMAS', measures=[dict(agg='count')], filters=[dict(column='BUYMAS.BUYERNAME', op='eq', value='NEXT')]),
        dict(base='ORDSIZE', measures=[dict(agg='sum', column='ORDSIZE.RQTY')], group_by=['SIZE_MAS.SIZES'],
             filters=[dict(column='ORDMAS.ODATE', op='between', value=['2026-01-01', '2026-03-31'])]),
        dict(base='ORDMAS', measures=[dict(agg='count')], group_by=[dict(column='ORDMAS.ODATE', bucket='month')]),
        dict(base='ORDMAS', filters=[dict(column='BUYMAS.BUYERNAME', op='contains', value='50%_off\\')]),
    ]
    for intent in intents:
        comp = compile_intent(intent, kb, d)
        assert set(policy.check(comp['sql'], 'oracle', OWNER)) == set(comp['tables'])
        assert policy.review(comp, kb, d)['status'] == 'passed'


def test_review_requires_a_row_limit_and_catalog_tables():
    kb = service.build_kb(service.snapshot(), registry.effective())
    d = Dialect('oracle', OWNER)
    comp = compile_intent(dict(base='ORDMAS', measures=[dict(agg='count')]), kb, d)
    with pytest.raises(policy.UnsafeSQL, match='row limit'):
        policy.review(dict(comp, sql=comp['sql'].split(' FETCH FIRST')[0]), kb, d)
    with pytest.raises(policy.UnsafeSQL, match='not in the catalog'):
        policy.review(dict(comp, sql='SELECT X FROM GARMENT_DEMO.NOT_A_TABLE FETCH FIRST 5 ROWS ONLY'), kb, d)


def test_executors_refuse_unsafe_sql_without_touching_the_connection():
    class Conn:
        call_timeout = 0
        def cursor(self): raise AssertionError('the database must not be reached')
        def rollback(self): pass
    with pytest.raises(policy.UnsafeSQL):
        service.OracleExecutor(Conn())('DELETE FROM GARMENT_DEMO.ORDMAS', {}, 1)
    import sqlite3
    with pytest.raises(policy.UnsafeSQL):
        service.SqliteExecutor(sqlite3.connect(':memory:'))('SELECT * FROM sqlite_master', {}, 1)
