"""Natural-language question engine: catalog, join paths, value linking, fan-out safety, compiled SQL
executed against a SQLite copy of the GARMENT_DEMO structure (tests/demo_db.py)."""
from contextlib import contextmanager
from datetime import date
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from backend.erp import mapping as registry
from backend.insight import engine, service, knowledge as K
from backend.insight.query import Dialect, QueryError, compile_intent
from backend.insight.understand import NotUnderstood, lexicon
import demo_db

TODAY = date(2026, 10, 17)


@pytest.fixture(scope='module')
def world():
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite')
    idx = {}
    for t, c in service.indexable(kb):
        for v, n in ex(f'SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}', {}, 5000):
            idx.setdefault(K.norm(v), []).append(dict(table=t, column=c, value=str(v), n=n))
    return dict(kb=kb, sources={'db': (ex, d), 'local': (ex, d)}, find=lambda p: idx.get(K.norm(p), []), lex=lexicon(kb), conn=conn)


def ask(w, q, **kw):
    return engine.ask(q, w['kb'], w['sources'], finder=w['find'], today=TODAY, lex=w['lex'], **kw)


def table(r):
    return [dict(zip([c['label'] for c in r['columns']], row)) for row in r['rows']]


def test_catalog_merges_dictionary_registry_and_curated_meaning(world):
    kb = world['kb']
    assert kb.table('ORDMAS')['business'] == 'Order' and kb.column('ORDMAS.BUYER')['references'] == 'BUYMAS'
    assert kb.column('ORDMAS.CREATEDBY')['role'] == 'system'
    size_link = next(j for j in kb.joins if j['child'] == 'ORDSIZE' and j['parent'] == 'ORDSTYLE')
    assert size_link['pairs'] == [['ORDMASID', 'ORDMASID'], ['SUB3_SUBNO', 'SUBNO']] and size_link['status'] == 'verified'
    # naming-convention links are kept but marked as inferred
    assert any(j['status'] == 'inferred' for j in kb.joins)
    # paths prefer many-to-one steps: size -> line -> colour, never through another order table
    assert [(j['parent'], d) for j, d in kb.path('ORDSIZE', 'COLORMAS')] == [('ORDSTYLE', 'up'), ('COLORMAS', 'up')]


def test_counts_with_value_linking_and_cancelled_rule(world):
    r = ask(world, 'How many orders for NEXT?')
    assert r['rows'] == [[2]] and r['intent']['base'] == 'ORDMAS'
    assert r['intent']['filters'] == [dict(column='BUYMAS.BUYERNAME', op='eq', value='NEXT')]
    assert 'COUNT(DISTINCT t0.ORDMASID)' in r['sql'] and ':b0' in r['sql'] and 'NEXT' not in r['sql']
    assert "CANCEL <> 'T'" in r['sql']                       # cancelled orders excluded by default
    assert ask(world, 'how many cancelled orders')['rows'] == [[1]]


def test_size_breakdown_uses_size_quantities_not_order_totals(world):
    r = ask(world, 'size wise quantity for order PK/26-27/S/9001')
    assert r['intent']['base'] == 'ORDSIZE' and r['intent']['group_by'] == ['SIZE_MAS.SIZES']
    assert {x['Size']: x['Total size quantity (pcs)'] for x in table(r)} == {'3-6M': 180, '6-9M': 220, '9-12M': 50}
    assert r['confidence']['level'] == 'high'


def test_totals_never_double_count_across_child_rows(world):
    kb = world['kb']
    with pytest.raises(QueryError, match='count them twice'):
        compile_intent(dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')], group_by=['SIZE_MAS.SIZES']), kb, Dialect('sqlite'))
    with pytest.raises(QueryError, match='counted twice'):
        compile_intent(dict(base='ORDSIZE', measures=[dict(agg='sum', column='ORDMAS.OQTY')]), kb, Dialect('sqlite'))
    # child filters become EXISTS, so each order is counted once
    r = engine.run(dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.OQTY')],
                        filters=[dict(column='SIZE_MAS.SIZES', op='eq', value='6-9M')]), kb, world['sources'])
    assert 'EXISTS' in r['sql'] and r['rows'] == [[450]]


def test_grouping_ranking_dates_and_comparisons(world):
    r = ask(world, 'top 1 buyers by quantity')
    assert table(r) == [{'Buyer name': 'NEXT', 'Total order quantity (pcs)': 950}]
    r = ask(world, 'orders this month')
    assert sorted(x['Order reference'] for x in table(r)) == ['PK/26-27/S/9001', 'PK/26-27/S/9003']
    r = ask(world, 'orders with quantity more than 460')
    assert [x['Order reference'] for x in table(r)] == ['PK/26-27/S/9002']
    r = ask(world, 'monthly order quantity')
    assert {x['Order date (month)']: x['Total order quantity (pcs)'] for x in table(r)} == {'2026-10': 550, '2026-09': 500}


def test_answers_across_linked_tables_with_master_labels(world):
    r = ask(world, 'which buyer has order PK/26-27/S/9003')
    assert table(r)[0]['Buyer name'] == 'ZARA'
    r = ask(world, 'colour wise quantity for NEXT')
    assert {x['Colour']: x['Total size quantity (pcs)'] for x in table(r)} == {'NAVY': 500, 'WHITE AOP': 300, 'PINK SOLID': 150}
    # every answer carries its provenance graph
    assert {n['table'] for n in r['graph']['nodes']} >= {'ORDSIZE', 'ORDSTYLE', 'COLORMAS', 'BUYMAS'}
    assert all(e['status'] for e in r['graph']['edges'])


def test_unverified_links_lower_confidence(world):
    r = ask(world, 'fabric kg by colour')
    assert r['confidence']['score'] < 0.9 and any('unconfirmed link' in x for x in r['confidence']['reasons'])


def test_adding_a_column_recompiles_with_the_right_join(world):
    r = ask(world, 'show orders of buyer NEXT')
    intent = dict(r['intent'], columns=[f'ORDMAS.{c}' for c in world['kb'].table('ORDMAS')['display']] + ['ORDMAS.MERCH'])
    r2 = engine.run(intent, world['kb'], world['sources'])
    assert table(r2)[0]['Merchandiser'] == 'PRIYA'


def test_unknown_questions_ask_for_clarification(world):
    with pytest.raises(NotUnderstood):
        ask(world, 'hello there')


def test_saved_examples_generalise_to_new_values(world):
    r = ask(world, 'size wise quantity for order PK/26-27/S/9001')
    ex = dict(id=1, masked=r['masked'], question=r['question'], intent=engine.generalize(r['intent'], r['linked_values']))
    r2 = ask(world, 'size wise quantity for order PK/26-27/S/9003', examples=[ex])
    assert r2['source'] == 'saved example' and r2['intent']['filters'][0]['value'] == 'PK/26-27/S/9003'
    assert sum(x['Total size quantity (pcs)'] for x in table(r2)) == 100


def test_only_select_statements_run(world):
    with pytest.raises(ValueError):
        service.SqliteExecutor(world['conn'])('DELETE FROM ORDMAS', {}, 1)


# --- API --------------------------------------------------------------------------------------------
PW = 'test-password-123'


@pytest.fixture
def api(tmp_path, monkeypatch, world):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(service, 'DATA', tmp_path)
    service.invalidate(); main.clear_counts()

    @contextmanager
    def sources():
        yield world['sources']
    monkeypatch.setattr(main, 'query_sources', sources)
    monkeypatch.setattr(main, 'knowledge_base', lambda: world['kb'])
    monkeypatch.setattr(service, 'finder', lambda kb: world['find'])
    monkeypatch.setattr(main.insight, 'finder', lambda kb: world['find'])
    monkeypatch.setattr(main.ai, 'available', lambda: False)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        yield c


def test_chat_api_answer_feedback_example_and_preview(api):
    r = api.post('/api/chat/ask', json={'question': 'size wise quantity for order PK/26-27/S/9001'}).json()
    assert r['kind'] == 'answer' and r['row_count'] == 3 and r['graph']['nodes']
    assert api.post(f'/api/chat/{r["id"]}/feedback', json={'correct': True}).json()['example']
    assert api.get('/api/chat/examples').json()[0]['question'].startswith('size wise')
    p = api.post('/api/chat/preview', json={'table': 'ORDSTYLE', 'intent': r['intent']}).json()
    assert p['scoped'] and len(p['rows']) == 2 and any(c['column'] == 'COMBO' for c in p['columns'])
    run = api.post('/api/chat/run', json={'intent': dict(r['intent'], group_by=r['intent']['group_by'] + ['COLORMAS.COLOR']),
                                          'question': r['question']}).json()
    assert run['kind'] == 'answer' and len(run['columns']) == 3
    bad = api.post('/api/chat/run', json={'intent': {'base': 'ORDMAS; DROP TABLE X'}}).json()
    assert bad['kind'] == 'refused'
    unclear = api.post('/api/chat/ask', json={'question': 'hello'}).json()
    assert unclear['kind'] == 'clarify' and unclear['suggestions']
    assert api.get('/api/chat/history').json()[0]['question'] == 'hello'


def test_knowledge_api_and_admin_edits(api):
    k = api.get('/api/knowledge').json()
    assert any(t['table'] == 'ORDMAS' and t['business'] == 'Order' for t in k['tables'])
    n = api.get('/api/knowledge/neighbours/ORDMAS').json()
    assert any(x['table'] == 'ORDSTYLE' and x['direction'] == 'down' for x in n['neighbours'])
    assert api.put('/api/knowledge/objects', json={'object': 'ORDMAS.REMARKS', 'synonyms': ['note']}).status_code == 200
    assert api.put('/api/knowledge/objects', json={'object': 'NOPE.X'}).status_code == 404
    assert any(a['action'] == 'knowledge_edit' for a in api.get('/api/audit').json())


def test_explorer_uses_catalog_names_decoded_masters_and_links(api):
    tables = {t['table']: t for t in api.get('/api/explorer/tables').json()}
    assert tables['ORDMAS']['business'] == 'Order' and tables['ORDMAS']['rows'] == 4 and tables['ORDMAS']['approx'] is False
    page = api.get('/api/explorer/tables/ORDMAS?limit=2').json()
    assert [r['ORDMASID'] for r in page['rows']] == [104, 103] and page['next_after'] == 103      # keyset paging
    assert page['decoded']['BUYER']['2'] == 'ZARA'                                               # master label, not raw ID
    nxt = api.get('/api/explorer/tables/ORDMAS?limit=2&after=103').json()
    assert [r['ORDMASID'] for r in nxt['rows']] == [102, 101]
    found = api.get('/api/explorer/tables/ORDMAS?column=DOCID&value=9003').json()
    assert found['total'] == 1 and found['rows'][0]['DOCID'] == 'PK/26-27/S/9003'
    rel = api.get('/api/explorer/related/ORDMAS/101').json()
    by = {g['table']: g for g in rel['related']}
    assert len(by['ORDSTYLE']['rows']) == 2 and by['ORDSTYLE']['direction'] == 'down'
    assert by['BUYMAS']['rows'][0]['BUYERNAME'] == 'NEXT' and by['BUYMAS']['direction'] == 'up'
    assert api.get('/api/explorer/tables/NOT_A_TABLE').status_code == 404
    assert api.get('/api/explorer/tables/ORDMAS?column=BAD&value=x').status_code == 404


def test_explorer_business_column_order_and_exact_order_lookup(api, world):
    page = api.get('/api/explorer/tables/ORDMAS?limit=5').json()
    assert page['display'] == world['kb'].table('ORDMAS')['display'] and page['display'][0] == 'DOCID' and page['label'] == 'DOCID'
    q = '/api/explorer/tables/ORDMAS?column={}&value={}{}'
    exact = api.get(q.format('DOCID', 'pk/26-27/s/9001', '&exact=true')).json()             # whole reference, any case
    assert exact['total'] == 1 and exact['rows'][0]['ORDMASID'] == 101
    assert api.get(q.format('DOCID', '9001', '&exact=true')).json()['total'] == 0              # part of it: not exact
    assert api.get(q.format('DOCID', '9001', '')).json()['total'] == 1                         # ...but contains finds it
    assert api.get(q.format('ORDMASID', '10', '')).json()['total'] == 4                        # 101-104 all contain "10"
    assert api.get(q.format('ORDMASID', '10', '&exact=true')).json()['total'] == 0
    assert [r['ORDMASID'] for r in api.get(q.format('ORDMASID', '101', '&exact=true')).json()['rows']] == [101]
    # a line's or size's order ID shows the order reference, not just the raw ID
    assert api.get('/api/explorer/tables/ORDSIZE?limit=50').json()['decoded']['ORDMASID']['101'] == 'PK/26-27/S/9001'


def test_explorer_order_view_nests_sizes_under_lines(api, monkeypatch):
    rel = api.get('/api/explorer/related/ORDMAS/101').json()
    assert rel['key'] == 'ORDMASID' and rel['label'] == 'DOCID' and rel['display'][0] == 'DOCID'
    assert {c['name'] for c in rel['columns']} >= {'DOCID', 'BUYER', 'OQTY'} and rel['decoded']['BUYER']['1'] == 'NEXT'
    by = {g['table']: g for g in rel['related']}
    sizes, lines = by['ORDSIZE'], by['ORDSTYLE']
    assert {k: sizes['under'][k] for k in ('table', 'pairs', 'status')} == dict(table='ORDSTYLE', pairs=[['ORDMASID', 'ORDMASID'], ['SUB3_SUBNO', 'SUBNO']], status='verified')
    assert lines['under'] is None and by['BUYMAS']['under'] is None and by['ORDYARN']['under']['table'] == 'ORDFAB'
    assert all(r['SUBNO'] for r in lines['rows']) and all(r['SUB3_SUBNO'] for r in sizes['rows'])   # rows carry the link values
    assert sum(r['RQTY'] for r in sizes['rows']) == 450 and sizes['total'] == 5 and not sizes['more']
    assert sizes['filter'] == {'column': 'ORDMASID', 'value': 101} and sizes['pairs'] == [['ORDMASID', 'ORDMASID']]
    assert sizes['decoded']['GSIZE']['31'] == '3-6M'
    assert all(len({c['name'] for c in g['columns']}) == len(g['columns']) for g in rel['related'])  # no column twice
    # a big order: the first rows, the exact total, and the full list through an exact filter
    monkeypatch.setattr(main, 'REL_ROWS', 2)
    sizes = {g['table']: g for g in api.get('/api/explorer/related/ORDMAS/101').json()['related']}['ORDSIZE']
    assert sizes['more'] and len(sizes['rows']) == 2 and sizes['total'] == 5
    assert api.get('/api/explorer/tables/ORDSIZE?column=ORDMASID&value=101&exact=true').json()['total'] == 5
    assert api.get('/api/explorer/related/ORDMAS/999').status_code == 404


def test_explorer_counts_are_approximate_only_for_very_large_unfiltered_tables(api, world, monkeypatch):
    monkeypatch.setitem(world['kb'].table('ORDSIZE'), 'rows', 5_000_000)
    page = api.get('/api/explorer/tables/ORDSIZE?limit=5').json()
    assert page['approx'] is True and page['total'] == 5_000_000                       # statistics, marked ≈ on screen
    page = api.get('/api/explorer/tables/ORDSIZE?column=ORDMASID&value=101&exact=true').json()
    assert page['approx'] is False and page['total'] == 5                                # a filter is always counted
