"""Answer verification (backend/insight/verify.py) and the check trace every answer carries (engine.run)."""
import pytest
from backend.erp import mapping as registry
from backend.insight import engine, service, verify
from backend.insight.query import Dialect, QueryError
import demo_db


def world(conn):
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite')
    return kb, {'db': (ex, d), 'local': (ex, d)}


BY_BUYER = dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')], group_by=['BUYMAS.BUYERNAME'])


def test_every_answer_carries_the_specialist_checks_in_order():
    kb, sources = world(demo_db.create())
    r = engine.run(BY_BUYER, kb, sources)
    stages = [c['stage'] for c in r['checks']]
    assert stages == ['understand', 'build', 'safety', 'join', 'execute', 'totals']
    assert all(c['status'] == 'passed' for c in r['checks']), r['checks']
    assert {tuple(row) for row in r['rows']} == {('NEXT', 950), ('ZARA', 100)}      # the cancelled order is excluded
    assert 'add up exactly' in r['checks'][-1]['detail'] and r['confidence']['level'] == 'high'


def test_a_parent_with_repeated_keys_stops_a_total_instead_of_double_counting():
    conn = demo_db.create()
    conn.execute("INSERT INTO BUYMAS (BUYMASID, BUYERNAME, CANCEL) VALUES (1, 'NEXT LTD', 'F')")   # second row for buyer id 1
    kb, sources = world(conn)
    with pytest.raises(QueryError, match='count rows twice'):
        engine.run(BY_BUYER, kb, sources)
    # a plain list is still shown, with a warning that some rows may repeat
    r = engine.run(dict(base='ORDMAS', columns=['ORDMAS.DOCID', 'BUYMAS.BUYERNAME']), kb, sources)
    join = next(c for c in r['checks'] if c['stage'] == 'join')
    assert join['status'] == 'warning' and 'appear twice' in join['detail'] and r['confidence']['score'] < 0.9


def test_unique_keys_from_oracle_make_the_probe_unnecessary():
    conn = demo_db.create()
    kb, sources = world(conn)
    kb.unique = {'BUYMAS': [{'BUYMASID'}]}
    seen = []
    ex, d = sources['db']
    spy = lambda sql, binds, n: seen.append(sql) or ex(sql, binds, n)
    r = engine.run(BY_BUYER, kb, {'db': (spy, d), 'local': (spy, d)})
    assert not any('HAVING COUNT(*) > 1' in s for s in seen)
    assert 'Oracle guarantees' in next(c for c in r['checks'] if c['stage'] == 'join')['detail']


def test_groups_that_overlap_report_the_total_counted_once():
    """Orders by colour: an order with two colours sits in two groups, so the groups add up to more than the
    number of orders. The cross-check finds the real number and the summary quotes it."""
    conn = demo_db.create()
    kb, sources = world(conn)
    r = engine.run(dict(base='ORDMAS', measures=[dict(agg='count')], group_by=['COLORMAS.COLOR']), kb, sources)
    totals = next(c for c in r['checks'] if c['stage'] == 'totals')
    real = conn.execute("SELECT COUNT(*) FROM ORDMAS WHERE CANCEL IS NULL OR CANCEL <> 'T'").fetchone()[0]
    assert sum(row[1] for row in r['rows']) == real + 1                    # one order has two colours
    assert totals['status'] == 'passed' and f'counted once each, is {real}' in totals['detail']
    assert f'All groups together: {real}.' in r['summary']                # not the overlapping sum


def test_a_total_that_does_not_reconcile_is_flagged_and_lowers_confidence():
    kb, sources = world(demo_db.create())
    comp = dict(columns=[dict(key='c0', kind='dimension'), dict(key='c1', kind='measure', agg='sum', label='Total order quantity')],
                truncated=False, limit=200)
    check = verify.cross_check(BY_BUYER, comp, [['NEXT', 900], ['ZARA', 100]], kb, *sources['db'])
    assert check['status'] == 'failed' and '1,000' in check['detail'] and '1,050' in check['detail']


def test_truncated_breakdowns_are_not_cross_checked():
    kb, sources = world(demo_db.create())
    comp = dict(columns=[dict(key='c1', kind='measure', agg='sum')], truncated=True, limit=5)
    assert verify.cross_check(BY_BUYER, comp, [[1, 1]], kb, *sources['db'])['status'] == 'skipped'
