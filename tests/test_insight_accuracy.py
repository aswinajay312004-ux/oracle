"""Answer correctness regressions using synthetic linked records, never client data."""
from datetime import date

import pytest

import demo_db
from backend.erp import mapping
from backend.insight import engine, service, understand
from backend.insight.query import Dialect, QueryError, compile_intent


@pytest.fixture
def world():
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), mapping.effective())
    sources = {'db': (service.SqliteExecutor(conn), Dialect('sqlite'))}
    yield conn, kb, sources
    conn.close()


def test_same_child_filters_use_each_columns_type_and_name(world):
    _, kb, sources = world
    intent = dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')], filters=[
        dict(column='ORDSIZE.GSIZE', op='eq', value=31),
        dict(column='ORDSIZE.RQTY', op='gte', value=90),
    ])
    result = engine.run(intent, kb, sources)
    assert result['rows'] == [[450]]
    assert '.GSIZE = ' in result['sql'] and '.RQTY >= ' in result['sql']
    nodes = {n['table'] for n in result['graph']['nodes']}
    assert {'ORDMAS', 'ORDSIZE'} <= nodes
    assert all(e['source'] in nodes and e['target'] in nodes for e in result['graph']['edges'])


def test_child_conditions_must_match_the_same_row(world):
    _, kb, sources = world
    result = engine.run(dict(base='ORDMAS', measures=[dict(agg='count')], filters=[
        dict(column='ORDSIZE.GSIZE', op='eq', value=31),
        dict(column='ORDSIZE.RQTY', op='gt', value=100),
    ]), kb, sources)
    assert result['rows'] == [[0]]  # size 31 has at most 100; other sizes exceed it


def test_ascending_summary_reports_actual_maximum(world):
    _, kb, sources = world
    result = engine.run(dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')],
                             group_by=['BUYMAS.BUYERNAME'], order_by=[dict(ref='m0', dir='asc')]), kb, sources)
    assert result['rows'][0] == ['ZARA', 100]
    assert 'Highest: NEXT with 950' in result['summary']


def test_limited_group_summary_does_not_claim_overall_total(world):
    _, kb, sources = world
    result = engine.run(dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')],
                             group_by=['BUYMAS.BUYERNAME'], order_by=[dict(ref='m0', dir='asc')], limit=1), kb, sources)
    assert result['truncated']
    assert 'Highest among shown groups: ZARA' in result['summary']
    assert 'All groups together' not in result['summary']


def test_date_not_equal_excludes_entire_day(world):
    conn, kb, sources = world
    conn.execute("UPDATE ORDMAS SET ODATE='2026-10-05 14:30:00' WHERE ORDMASID=101")
    result = engine.run(dict(base='ORDMAS', measures=[dict(agg='count')], filters=[
        dict(column='ORDMAS.ODATE', op='ne', value='2026-10-05'),
    ]), kb, sources)
    assert result['rows'] == [[2]]
    compiled = compile_intent(result['intent'], kb, Dialect('oracle', 'GARMENT_DEMO'))
    assert ' OR ' in compiled['sql'] and len(compiled['binds']) == 2


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_numbers_are_rejected(world, value):
    _, kb, _ = world
    with pytest.raises(QueryError, match='finite number'):
        compile_intent(dict(base='ORDMAS', filters=[dict(column='ORDMAS.OQTY', op='eq', value=value)]), kb, Dialect('sqlite'))


def test_group_threshold_is_never_silently_dropped(world):
    _, kb, sources = world
    with pytest.raises(QueryError, match='comparison'):
        engine.ask('buyer wise total order quantity above 500', kb, sources, finder=lambda _: [],
                   today=date(2026, 10, 19), ai_chat=lambda _: pytest.fail('AI must not bypass an unsupported constraint'))


def test_date_constraint_without_date_column_is_not_ignored(world):
    _, kb, _ = world
    with pytest.raises(QueryError, match='no date'):
        understand.interpret('buyers this month', kb, lambda _: [], today=date(2026, 10, 19))


@pytest.mark.parametrize('child_agg', ['count', 'min', 'max'])
def test_mixed_parent_and_child_measures_cannot_multiply_parent_total(world, child_agg):
    _, kb, _ = world
    with pytest.raises(QueryError, match='repeat its total'):
        compile_intent(dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY'),
                                                     dict(agg=child_agg, column='ORDSIZE.RQTY')]), kb, Dialect('sqlite'))


@pytest.mark.parametrize('value', [[], list(range(51)), 'NEXT'])
def test_selection_is_never_silently_shortened(world, value):
    _, kb, _ = world
    with pytest.raises(QueryError, match='between 1 and 50'):
        compile_intent(dict(base='ORDMAS', filters=[dict(column='ORDMAS.BUYER', op='in', value=value)]), kb, Dialect('sqlite'))


def test_unsupported_aggregate_sort_is_not_ignored(world):
    _, kb, _ = world
    with pytest.raises(QueryError, match='displayed column'):
        compile_intent(dict(base='ORDMAS', measures=[dict(agg='count')],
                            order_by=[dict(ref='ORDSIZE.RQTY')]), kb, Dialect('sqlite'))
