"""Large-database behaviour (docs/PERFORMANCE.md): index-friendly live lookups with a time budget, a value
index that keeps the newest values of big columns, time-limited counts, explorer filters that an index can
answer, plain-English timeouts and sampled discovery. The fast tests use SQLite and fakes; the last section
runs on the local Oracle when POPPYS_TEST_ORACLE=1."""
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from backend.erp import mapping as registry
from backend.insight import engine, service, understand
from backend.insight.query import Dialect, QueryError, compile_intent
from scripts import discover_schema as disc
import demo_db

ORACLE = Dialect('oracle', 'GARMENT_DEMO')
BIG = {'ORDMAS': 3_000_000, 'ORDSTYLE': 6_000_000, 'ORDSIZE': 24_000_000, 'BUYMAS': 4, 'STYLENUM': 3, 'STYLEMAS': 2, 'SIZE_MAS': 5,
       'COLORMAS': 3, 'UNITMAS': 2, 'MERCHMAS': 1, 'PARTYMAS': 0, 'ORDFAB': 5, 'ORDYARN': 5, 'ORDACC': 0}


def big_kb(indexed=('ORDMAS.DOCID', 'ORDMAS.ORDMASID'), stats=BIG):
    phys = service.snapshot(); phys['stats'] = dict(stats); phys.pop('indexed')
    if indexed is not None:   # None: a cache harvested before index information was kept
        phys['indexed'] = list(indexed)
    return service.build_kb(phys, registry.effective())


class Spy:
    """Records every statement and the time limit it ran with; answers from an inner executor or a function."""

    def __init__(self, inner=None, answer=None):
        self.inner = inner; self.answer = answer; self.sql = []; self.limits = []

    def __call__(self, sql, binds, n, timeout_ms=None):
        self.sql.append(sql); self.limits.append(timeout_ms)
        return self.answer(sql, binds, n, timeout_ms) if self.answer else self.inner(sql, binds, n)

    def limited(self, ms):
        return lambda sql, binds, n: self(sql, binds, n, timeout_ms=ms)


def on(sql, *tables):
    return any(f'GARMENT_DEMO.{t} ' in sql for t in tables)


# --- live lookups -----------------------------------------------------------------------------------
def test_lookup_plan_puts_cheap_lookups_first_and_skips_closed_category_lists():
    small, indexed, scan = ({(t, c) for t, c, _ in tier} for tier in service.lookup_plan(big_kb(), closed={'ORDMAS.SAMPLETYPE', 'ORDMAS.DOCID'}))
    assert ('BUYMAS', 'BUYERNAME') in small and ('STYLENUM', 'STYLENUMBER') in small
    assert indexed == {('ORDMAS', 'DOCID')}                          # an index probe is cheap: never skipped
    assert ('ORDMAS', 'BONO') in scan and ('ORDMAS', 'SAMPLETYPE') not in scan | indexed | small
    unknown = service.lookup_plan(big_kb(indexed=None), closed=set())   # harvested before index info: try every big column as indexed
    assert ('ORDMAS', 'BONO') in {(t, c) for t, c, _ in unknown[1]} and not unknown[2]


def test_live_finder_uses_the_index_and_never_upper_trim_on_big_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)   # no value-index meta: nothing is known to be complete
    spy = Spy(answer=lambda sql, binds, n, ms: [('HX/0123456',)] if 'DOCID' in sql and 'HX/0123456' in binds.values() else [])
    find = service.live_finder(big_kb(), spy, ORACLE)
    assert find('hx/0123456') == [dict(table='ORDMAS', column='DOCID', value='HX/0123456', n=1)]
    big = [(s, ms) for s, ms in zip(spy.sql, spy.limits) if on(s, 'ORDMAS', 'ORDSTYLE', 'ORDSIZE')]
    assert big and all('UPPER' not in s and 'TRIM' not in s and 'IN (:v0, :v1)' in s for s, _ in big)
    assert all(ms and ms <= service.LOOKUP_MS for _, ms in big)       # each big-table lookup has a short time limit
    assert not any('BONO' in s for s in spy.sql)                     # the index answered: no full scan of other columns
    assert all('UPPER(TRIM(' in s for s in spy.sql if on(s, 'BUYMAS'))   # small tables keep the forgiving match
    n = len(spy.sql); find('hx/0123456'); assert len(spy.sql) == n   # the same word is looked up once


def test_unknown_word_scans_only_partial_columns_and_numbers_only_for_digits(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    (tmp_path / service.VALUE_META).write_text(json.dumps(dict(   # BONO is complete and short, but holds codes: still probed
        complete=['ORDMAS.SAMPLETYPE', 'ORDMAS.ORDCATEGORY', 'ORDMAS.BONO'], counts={'ORDMAS.SAMPLETYPE': 2, 'ORDMAS.ORDCATEGORY': 3, 'ORDMAS.BONO': 500},
        words=['ORDMAS.SAMPLETYPE', 'ORDMAS.ORDCATEGORY'])))
    assert service.closed_lists() == {'ORDMAS.SAMPLETYPE', 'ORDMAS.ORDCATEGORY'}
    spy = Spy(answer=lambda *a: [])
    assert service.live_finder(big_kb(), spy, ORACLE)('PK/26-27/S/9999') == []
    probed = {c for s in spy.sql if on(s, 'ORDMAS', 'ORDSTYLE') for c in ('DOCID', 'BONO', 'SAMPLETYPE', 'ORDCATEGORY', 'COMBO') if f'WHERE {c} ' in s}
    assert probed == {'DOCID', 'BONO'}
    spy.sql.clear(); service.live_finder(big_kb(), spy, ORACLE)('X' * 40 + '1')   # longer than DOCID (30): not looked up there
    assert not any('WHERE DOCID ' in s for s in spy.sql) and any('WHERE BONO ' in s for s in spy.sql)


def test_a_short_code_column_is_never_a_closed_list(tmp_path, monkeypatch):
    """A sparsely filled buyer PO column had 500 values at refresh time: codes added since must still be found live."""
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    (tmp_path / service.VALUE_META).write_text(json.dumps(dict(complete=['ORDMAS.BONO'], counts={'ORDMAS.BONO': 500})))
    assert service.closed_lists() == set()                               # no word information: nothing is closed
    assert ('ORDMAS', 'BONO') in {(t, c) for t, c, _ in service.lookup_plan(big_kb())[2]}
    assert service._looks_like_words(['BULK ORDER', 'DEVELOPMENT SAMPLE', 'SALESMAN SAMPLE'])
    assert not service._looks_like_words(['PO-150', 'PO-151', 'BULK']) and not service._looks_like_words([])


def test_live_lookups_share_one_budget_per_question_not_per_word(tmp_path, monkeypatch):
    """Four unknown codes in one question cost one budget, not four, and later words keep their index probes."""
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    probes = []

    def slow(sql, binds, n, ms):
        probes.append((sql, ms))
        if on(sql, 'ORDMAS', 'ORDSTYLE', 'ORDSIZE') and 'WHERE DOCID ' not in sql:   # full scans of unindexed big columns time out
            time.sleep(min(ms, 120) / 1000); raise service.TimedOut(ms / 1000)
        return []
    find = service.live_finder(big_kb(), Spy(answer=slow), ORACLE, budget_ms=600)
    started = time.time()
    for word in ('PK/X/1', 'PK/X/2', 'PK/X/3', 'PK/X/4'):
        assert find(word) == []
    assert time.time() - started < 600 / 1000 + 0.6, 'one budget for the whole question'
    assert find.budget['left'] < 600 and find.skipped >= 1
    docid = [s for s, _ in probes if 'WHERE DOCID ' in s]
    assert len(docid) == 4, 'every word still gets its cheap index probe (the reserve)'


def test_live_lookups_share_one_time_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)

    def slow(sql, binds, n, ms):
        if ms:   # a big-table lookup that runs out its time limit
            time.sleep(min(ms, 150) / 1000); raise service.TimedOut(ms / 1000)
        return []
    find = service.live_finder(big_kb(indexed=()), Spy(answer=slow), ORACLE, budget_ms=300)
    started = time.time()
    assert find('PK/26-27/S/9999') == []
    assert time.time() - started < 1.5 and find.skipped >= 1


# --- Ask AI compiled SQL on big tables ---------------------------------------------------------------
SIZE_WISE = dict(base='ORDSIZE', measures=[dict(agg='sum', column='ORDSIZE.RQTY')], group_by=['SIZE_MAS.SIZES'],
                 filters=[dict(column='ORDMAS.DOCID', op='eq', value='PK/26-27/S/9001')])


def test_text_equality_on_a_big_table_compiles_to_the_plain_indexed_column():
    """UPPER(TRIM(DOCID)) = :b took 9.6-11.3 s at 3M orders (no index can answer it); DOCID = :b took 0.002 s."""
    comp = compile_intent(SIZE_WISE, big_kb(), ORACLE)
    assert 't2.DOCID = :b0' in comp['sql'] and comp['binds'] == {'b0': 'PK/26-27/S/9001'}
    assert 'UPPER' not in comp['sql'] and 'TRIM' not in comp['sql']
    # typed in lower case: the value as typed and in capitals, still one plain-column predicate
    typed = compile_intent(dict(SIZE_WISE, filters=[dict(column='ORDMAS.DOCID', op='eq', value=' pk/26-27/s/9001 ')]), big_kb(), ORACLE)
    assert 't2.DOCID IN (:b0, :b1)' in typed['sql'] and typed['binds'] == {'b0': 'pk/26-27/s/9001', 'b1': 'PK/26-27/S/9001'}


def test_big_table_filters_match_every_spelling_the_value_index_knows():
    seen = []

    def stored(table, column, value):
        seen.append((table, column, value))
        return ['Bulk Order', 'BULK ORDER', 'bulk  order'] if column == 'SAMPLETYPE' else []
    intent = dict(base='ORDMAS', measures=[dict(agg='count', column=None)],
                  filters=[dict(column='ORDMAS.SAMPLETYPE', op='eq', value='bulk order'), dict(column='ORDMAS.BONO', op='in', value=['po-1', 'PO-2'])])
    comp = compile_intent(intent, big_kb(), ORACLE, stored=stored)
    assert 't0.SAMPLETYPE IN (:b0, :b1, :b2)' in comp['sql'] and 't0.BONO IN (:b3, :b4, :b5)' in comp['sql']
    assert [comp['binds'][f'b{i}'] for i in range(6)] == ['bulk order', 'Bulk Order', 'BULK ORDER', 'po-1', 'PO-1', 'PO-2']
    assert ('ORDMAS', 'SAMPLETYPE', 'bulk order') in seen and 'UPPER' not in comp['sql']   # 'bulk  order' is another value


def test_small_tables_keep_the_forgiving_match_and_char_columns_are_padded():
    intent = dict(base='ORDMAS', measures=[dict(agg='count', column=None)],
                  filters=[dict(column='BUYMAS.BUYERNAME', op='eq', value='next'), dict(column='ORDMAS.CANCEL', op='eq', value='T')],
                  include_cancelled=True)
    comp = compile_intent(intent, big_kb(), ORACLE)
    assert 'UPPER(TRIM(t1.BUYERNAME)) = :b0' in comp['sql']               # 4 buyers: a full scan costs nothing
    assert 't0.CANCEL = RPAD(:b1, 1)' in comp['sql']                      # CHAR(1) on a big table: blank-padded, indexable
    sqlite = compile_intent(intent, big_kb(), Dialect('sqlite'))
    assert 't0.CANCEL = :b1' in sqlite['sql']                             # SQLite stores CHAR values unpadded
    unknown = compile_intent(SIZE_WISE, big_kb(stats={}), ORACLE)          # no statistics (never analysed): size unknown
    assert 't2.DOCID = :b0' in unknown['sql']
    small = compile_intent(SIZE_WISE, big_kb(stats=dict(BIG, ORDMAS=50_000)), ORACLE)
    assert 'UPPER(TRIM(t2.DOCID)) = :b0' in small['sql']
    too_long = compile_intent(dict(intent, filters=[dict(column='ORDMAS.CANCEL', op='eq', value='TOO LONG')]), big_kb(), ORACLE)
    assert '1 = 0' in too_long['sql']                                    # cannot be stored in CHAR(1): matches nothing


def test_live_finder_hit_on_a_big_table_compiles_to_one_index_probe(tmp_path, monkeypatch):
    """'size wise quantity for order X' end to end: the live finder finds X through the DOCID index, and the
    answer's SQL filters the plain DOCID column with the stored value."""
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    spy = Spy(answer=lambda sql, binds, n, ms: [('HX/0123456',)] if 'WHERE DOCID' in sql and 'HX/0123456' in binds.values() else [])
    kb = big_kb()
    r = engine.ask('size wise quantity for order hx/0123456', kb, {'db': (spy, ORACLE), 'local': (spy, ORACLE)},
                   finder=lambda p: [], live_finder=service.live_finder(kb, spy, ORACLE), lex=understand.lexicon(kb))
    assert r['intent']['filters'] == [dict(column='ORDMAS.DOCID', op='eq', value='HX/0123456')]
    assert 'DOCID = :b0' in r['sql'] and 'UPPER' not in r['sql'] and r['binds'] == {'b0': 'HX/0123456'}


# --- executor time limits ---------------------------------------------------------------------------
class FakeConn:
    """Enough of an oracledb connection to see call_timeout, rollbacks and a DPY-4024 call timeout."""

    class Err:
        full_code = 'DPY-4024'

    def __init__(self):
        self.call_timeout = 0; self.seen = []; self.rolled = 0

    def cursor(self):
        conn = self

        class Cursor:
            arraysize = 0

            def execute(self, sql, binds=None):
                conn.seen.append((sql, conn.call_timeout))
                if sql.startswith('SELECT'):
                    raise RuntimeError(FakeConn.Err())

            def fetchmany(self, n):
                return []
        return Cursor()

    def rollback(self):
        self.rolled += 1


def test_oracle_executor_turns_a_call_timeout_into_a_plain_message_and_restores_the_limit():
    conn = FakeConn(); ex = service.OracleExecutor(conn, timeout_ms=20000)
    with pytest.raises(QueryError, match='more than 2.5 seconds') as err:
        ex.limited(2500)('SELECT 1 FROM DUAL', {}, 1)
    assert isinstance(err.value, service.TimedOut) and 'DPY' not in str(err.value)
    assert conn.seen[-1] == ('SELECT 1 FROM DUAL', 2500) and conn.call_timeout == 20000 and conn.rolled == 1
    with pytest.raises(QueryError, match='more than 20 seconds'):
        ex('SELECT 1 FROM DUAL', {}, 1)


def test_query_time_limit_is_configurable(monkeypatch):
    monkeypatch.setenv('POPPYS_QUERY_TIMEOUT_S', '45')
    assert service._ms('QUERY_TIMEOUT_S', 20) == 45000
    monkeypatch.setenv('POPPYS_QUERY_TIMEOUT_S', 'soon')
    assert service._ms('QUERY_TIMEOUT_S', 20) == 20000


@pytest.mark.parametrize('raw', ['inf', '-inf', 'nan', '1e12', '4294968', '0', '-5', '0.5', '601'])
def test_query_time_limit_out_of_bounds_is_refused_with_a_log_line(monkeypatch, caplog, raw):
    monkeypatch.setenv('POPPYS_QUERY_TIMEOUT_S', raw)
    with caplog.at_level('WARNING', logger='poppys.insight'):
        assert service._ms('QUERY_TIMEOUT_S', 20) == 20000
    assert f'QUERY_TIMEOUT_S={raw!r}' in caplog.text and 'from 1 to 600' in caplog.text and 'default, 20 s' in caplog.text
    monkeypatch.setenv('POPPYS_QUERY_TIMEOUT_S', '600')
    assert service._ms('QUERY_TIMEOUT_S', 20) == 600_000


def test_an_infinite_time_limit_does_not_stop_the_app_from_starting():
    env = dict(os.environ, POPPYS_QUERY_TIMEOUT_S='inf')
    r = subprocess.run([sys.executable, '-c', 'from backend.insight import service; print(service.TIMEOUT_MS)'],
                       cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == '20000', r.stderr
    assert "Ignoring QUERY_TIMEOUT_S='inf'" in r.stderr                    # the startup log says why


# --- value index ------------------------------------------------------------------------------------
def test_value_index_keeps_newest_values_of_partial_columns_and_indexes_big_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); local_store.init()
    monkeypatch.setattr(service, 'VALUE_CAP', 4)
    conn = demo_db.create()
    for i in range(5):
        conn.execute('INSERT INTO ORDMAS (ORDMASID, DOCID, BONO, SAMPLETYPE) VALUES (?, ?, ?, ?)', (900 + i, f'NEW/{i}', f'PO-N{i}', 'BULK'))
    r = service.refresh_values(big_kb(), service.SqliteExecutor(conn), Dialect('sqlite'))
    assert not r['skipped'] and {'ORDMAS.DOCID', 'ORDMAS.BONO'} <= set(r['partial'])
    with local_store.db() as c:
        docids = {x[0] for x in c.execute("SELECT value FROM value_index WHERE tbl='ORDMAS' AND col='DOCID'")}
        types = {x[0] for x in c.execute("SELECT value FROM value_index WHERE tbl='ORDMAS' AND col='SAMPLETYPE'")}
    assert docids == {'NEW/1', 'NEW/2', 'NEW/3', 'NEW/4'}             # the newest orders, not an arbitrary 4
    assert types == {'DEVELOPMENT SAMPLE', 'BULK'}                    # a 3M-row order header is still indexed
    closed = service.closed_lists()
    assert 'ORDMAS.SAMPLETYPE' in closed and 'BUYMAS.BUYERNAME' in closed and 'ORDMAS.DOCID' not in closed


# --- explorer and counts ----------------------------------------------------------------------------
PW = 'test-password-123'


@pytest.fixture
def explorer(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(service, 'DATA', tmp_path)
    conn = demo_db.create()
    conn.execute("INSERT INTO ORDMAS (ORDMASID, DOCID, BONO) VALUES (150, 'Pk/Mixed/1', 'PO-150')")
    state = dict(kb=service.build_kb(service.snapshot(), registry.effective()), spy=Spy(service.SqliteExecutor(conn)))

    @contextmanager
    def sources():
        yield {'db': (state['spy'], Dialect('sqlite')), 'local': (state['spy'], Dialect('sqlite'))}
    monkeypatch.setattr(main, 'query_sources', sources)
    monkeypatch.setattr(main, 'knowledge_base', lambda: state['kb'])
    main.clear_counts()
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
        state['spy'].sql.clear()
        yield c, state


def rows(c, table, **params):
    r = c.get(f'/api/explorer/tables/{table}', params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_exact_text_filter_uses_the_plain_column_and_needs_no_count(explorer):
    c, s = explorer
    r = rows(c, 'ORDMAS', column='DOCID', value='pk/26-27/s/9001', exact='true')
    assert [x['DOCID'] for x in r['rows']] == ['PK/26-27/S/9001'] and r['total'] == 1 and not r['approx']
    assert any('DOCID IN (:v0, :v1)' in q for q in s['spy'].sql)
    assert not any('COUNT(' in q for q in s['spy'].sql) and not any('UPPER(CAST(DOCID' in q for q in s['spy'].sql)
    mixed = rows(c, 'ORDMAS', column='DOCID', value='PK/MIXED/1', exact='true')   # stored in mixed case: forgiving match
    assert [x['DOCID'] for x in mixed['rows']] == ['Pk/Mixed/1']


def test_exact_text_filter_on_a_very_large_table_stays_index_friendly(explorer):
    c, s = explorer
    s['kb'] = big_kb()
    assert rows(c, 'ORDMAS', column='DOCID', value='PK/26-27/S/9001', exact='true')['total'] == 1
    assert rows(c, 'ORDMAS', column='DOCID', value='PK/MIXED/1', exact='true')['rows'] == []   # no full-table UPPER() scan
    assert not any('UPPER(' in q for q in s['spy'].sql)


def test_contains_filter_escapes_wildcards(explorer):
    c, _ = explorer
    assert rows(c, 'ORDMAS', column='DOCID', value='%')['rows'] == []
    assert rows(c, 'ORDMAS', column='DOCID', value='_')['rows'] == []
    assert len(rows(c, 'ORDMAS', column='DOCID', value='26-27/S/')['rows']) == 4


def test_contains_filter_on_a_very_large_table_skips_the_count_and_explains_a_timeout(explorer):
    c, s = explorer
    s['kb'] = big_kb()
    r = rows(c, 'ORDMAS', column='DOCID', value='PK/', limit=2)
    assert len(r['rows']) == 2 and r['total'] is None and r['approx'] and r['next_after']
    assert not any('COUNT(' in q for q in s['spy'].sql)
    inner = s['spy'].inner

    def slow_like(sql, binds, n, ms):
        if 'LIKE' in sql:
            raise service.TimedOut(20)
        return inner(sql, binds, n)
    s['spy'].answer = slow_like
    r = c.get('/api/explorer/tables/ORDSIZE', params=dict(column='SUB3_SUBNO', value='1012'))
    assert r.status_code == 409 and '“is exactly”' in r.json()['detail'] and 'every row of Size quantity' in r.json()['detail']
    assert c.get('/api/explorer/tables/ORDSIZE', params=dict(after='abc')).status_code == 422


def test_load_more_reuses_the_filtered_count_of_the_first_page(explorer):
    """Each 'Load more' page of a filtered result used to run the filtered COUNT(*) again (up to 4 s each on a big
    table, even when it had already run out of time). It is counted once, on the first page."""
    c, s = explorer
    first = rows(c, 'ORDSIZE', column='ORDMASID', value='101', exact='true', limit=2)
    assert first['total'] == 5 and first['rows_known'] and first['next_after']
    page, after = first, first['next_after']
    while page['next_after']:
        page = rows(c, 'ORDSIZE', column='ORDMASID', value='101', exact='true', limit=2, after=page['next_after'])
        assert page['total'] == 5 and page['rows_known']
    assert after and sum('COUNT(' in q for q in s['spy'].sql) == 1
    rows(c, 'ORDSIZE', column='ORDMASID', value='101', exact='true', limit=2)   # filtering again counts again
    assert sum('COUNT(' in q for q in s['spy'].sql) == 2


def test_a_count_that_runs_out_says_so_and_is_not_retried_on_every_page(explorer):
    c, s = explorer
    inner = s['spy'].inner

    def slow_count(sql, binds, n, ms):
        if 'COUNT(' in sql:
            raise service.TimedOut(4)
        return inner(sql, binds, n)
    s['spy'].answer = slow_count
    first = rows(c, 'ORDSIZE', column='ORDMASID', value='101', exact='true', limit=2)
    assert first['total'] is None and first['rows_known'] is False and first['approx']   # 'count unavailable', never a guess
    rows(c, 'ORDSIZE', column='ORDMASID', value='101', exact='true', limit=2, after=first['next_after'])
    assert sum('COUNT(' in q for q in s['spy'].sql) == 1
    main.REL_ROWS, old = 2, main.REL_ROWS                 # the linked-records view of a big order counts too
    try:
        rel = c.get('/api/explorer/related/ORDMAS/101').json()
    finally:
        main.REL_ROWS = old
    sizes = {g['table']: g for g in rel['related']}['ORDSIZE']
    assert sizes['more'] and sizes['total'] is None and sizes['rows_known'] is False
    assert all(g['rows_known'] for g in rel['related'] if g['table'] != 'ORDSIZE')


def test_counts_are_time_limited_and_fall_back_honestly():
    def boom(sql, binds, n):
        raise service.TimedOut(4)
    d = Dialect('sqlite')
    assert main._count(boom, d, 'ORDMAS', {'rows': 500}, ' WHERE X = :v', {'v': 1}) == (None, True)   # never the whole-table figure
    assert main._count(boom, d, 'ORDMAS', {'rows': 500}) == (500, True)
    spy = Spy(answer=lambda *a: [(7,)])
    assert main._count(spy, d, 'ORDMAS', {'rows': 5}) == (7, False) and spy.limits == [4000]


def test_table_counts_share_a_time_budget():
    main.clear_counts()
    kb = big_kb(stats={t: 10 for t in BIG})

    def slow(sql, binds, n, ms):
        time.sleep(0.15); return [(10,)]
    started = time.time()
    out = main.table_counts(kb, {'db': (Spy(answer=slow), Dialect('sqlite'))}, list(BIG), budget_ms=400)
    assert time.time() - started < 1.5 and set(out) == set(BIG)
    assert any(not approx for _, approx in out.values()) and any(approx for _, approx in out.values())
    main.clear_counts()


# --- value index at startup -------------------------------------------------------------------------
@pytest.fixture
def values_state(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(service, 'DATA', tmp_path); local_store.init()
    monkeypatch.setattr(main, '_VALUES', dict(main._VALUES, state='not started', at=0.0, error=None, values=None))
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), registry.effective()); kb.live = True   # as if harvested from Oracle
    spy = Spy(service.SqliteExecutor(conn))

    @contextmanager
    def sources():
        yield {'db': (spy, Dialect('sqlite')), 'local': (spy, Dialect('sqlite'))}
    monkeypatch.setattr(main, 'query_sources', sources); monkeypatch.setattr(main, 'knowledge_base', lambda: kb)
    return kb, spy, sources


def test_value_index_is_built_in_the_background_at_startup(values_state, monkeypatch):
    kb, spy, _ = values_state
    assert main.start_value_index() is None and main.value_index_status()['state'] == 'off'   # never under pytest
    monkeypatch.delenv('PYTEST_CURRENT_TEST')
    monkeypatch.setenv('POPPYS_VALUE_INDEX_AT_STARTUP', '0')
    assert main.start_value_index() is None
    monkeypatch.setenv('POPPYS_VALUE_INDEX_AT_STARTUP', '1')
    thread = main.start_value_index()
    assert thread is not None and thread.daemon
    thread.join(10)
    status = main.value_index_status()
    assert status['state'] == 'ready' and status['source'] == 'startup' and status['values'] > 0 and status['finished_at']
    assert local_store.value_stats()
    n = len(spy.sql); thread = main.start_value_index(); thread.join(10)        # already built: nothing is read again
    assert len(spy.sql) == n and main.value_index_status()['source'] == 'earlier refresh'


def test_a_question_does_not_wait_for_the_background_build_and_builds_only_as_a_fallback(values_state, monkeypatch):
    kb, spy, sources = values_state
    with sources() as src:
        main._values_idle.clear(); main._VALUES['state'] = 'building'           # the startup build is still running
        monkeypatch.setattr(main, 'VALUES_WAIT_S', 0.2)
        started = time.time(); main._ensure_values(kb, src)
        assert time.time() - started < 1 and not local_store.value_stats() and not spy.sql   # waited briefly, built nothing
        main._values_idle.set(); main._VALUES['state'] = 'failed'; main._VALUES['at'] = time.time()
        main._ensure_values(kb, src)
        assert not spy.sql, 'right after a failed build a question does not try again'
        main._VALUES['at'] = 0.0
        main._ensure_values(kb, src)                                              # fallback: the question builds it
        assert local_store.value_stats() and main.value_index_status()['source'] == 'first question'
        n = len(spy.sql); main._ensure_values(kb, src)
        assert len(spy.sql) == n


def test_knowledge_api_reports_the_value_index_status(explorer):
    c, _ = explorer
    k = c.get('/api/knowledge').json()
    assert {'state', 'source', 'started_at', 'finished_at', 'values', 'error'} <= set(k['value_index'])


# --- discovery ----------------------------------------------------------------------------------------
def test_discovery_samples_big_tables_and_profiles_all_columns_in_two_passes():
    frm, pct = disc.source('ORDSIZE', 24_000_000, 200_000)
    assert 'SAMPLE BLOCK (0.833333) SEED (42)' in frm and pct == 0.8333
    assert disc.source('ORDFAB', 5, 200_000) == ('GARMENT_DEMO."ORDFAB"', None)
    cols = [dict(column_name='DOCID', data_type='VARCHAR2'), dict(column_name='OQTY', data_type='NUMBER'), dict(column_name='ODATE', data_type='DATE')]
    seen = []

    def run(what, sql, **binds):
        seen.append(sql)
        return [(100, 100, 98, 50, 3, 0, 0)] if 'fill' in what else [(0, 'HX/1'), (1, '400'), (1, '.5')]
    out, _, pct = disc.profile(run, 'ORDMAS', cols, 3_000_000, {('ORDMAS', 'DOCID'): dict(num_distinct=2_999_000)}, 200_000)
    assert len(seen) == 2 and all('SAMPLE BLOCK' in q for q in seen) and 'UNPIVOT' in seen[1]
    assert out['DOCID'] == dict(rows=3_000_000, filled=3_000_000, fill_pct=100.0, distinct=2_999_000, samples=['HX/1'],
                                sampled_pct=pct, distinct_from='statistics')
    assert out['OQTY']['fill_pct'] == 50.0 and out['OQTY']['samples'] == [400, 0.5] and out['OQTY']['distinct_from'] == 'sample'
    assert out['ODATE']['fill_pct'] == 0.0 and "YYYY-MM-DD" in seen[1]


# --- real Oracle (opt-in) -------------------------------------------------------------------------
oracle_only = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')


@oracle_only
def test_oracle_call_timeout_is_plain_and_connection_stays_usable():
    from backend import oracle_store
    with oracle_store.connect() as conn:
        ex = service.OracleExecutor(conn)
        with pytest.raises(service.TimedOut):
            ex.limited(1000)('SELECT COUNT(*) FROM all_objects a, all_objects b, all_objects c', {}, 1)
        assert ex('SELECT 1 FROM DUAL', {}, 1) == [(1,)] and conn.call_timeout == service.TIMEOUT_MS


@oracle_only
def test_replica_order_references_stay_unique_past_ten_million_orders():
    """tests/oracle_replica.py builds DOCID as HX/ + the order number; LPAD(n, 7) cut 10,000,000 to HX/1000000."""
    from backend import oracle_store
    import oracle_replica
    expr = 'LPAD(o.l, GREATEST(7, LENGTH(o.l)), \'0\')'
    assert expr in (Path(oracle_replica.__file__).read_text(encoding='utf-8'))
    with oracle_store.connect() as conn:
        c = conn.cursor()
        c.execute(f"SELECT 'HX/' || {expr} FROM (SELECT COLUMN_VALUE l FROM TABLE(sys.odcinumberlist(1, 123456, 1000000, 10000000, 12000000))) o")
        assert [r[0] for r in c] == ['HX/0000001', 'HX/0123456', 'HX/1000000', 'HX/10000000', 'HX/12000000']


@oracle_only
def test_oracle_harvest_lists_indexes_and_live_lookups_stay_within_budget():
    from backend import oracle_store
    from backend.config import OWNER
    with oracle_store.connect() as conn:
        phys = service.harvest(conn)
        assert any(x.startswith('ORDMAS.') for x in phys['indexed'])
        kb = service.build_kb(phys, oracle_store.current_mapping())
        find = service.live_finder(kb, service.OracleExecutor(conn), Dialect('oracle', OWNER))
        started = time.time()
        assert find('ZZ/NOPE/987654') == []
        assert time.time() - started < service.LOOKUP_BUDGET_MS / 1000 + 5


def test_duplicate_order_check_uses_the_index_on_a_large_ordmas():
    """order_ref_exists runs on every preview and save: on a large ORDMAS it must never scan the table."""
    from backend.erp.catalog import OracleCatalog

    class Cursor:
        def __init__(self, stats):
            self.sql = []; self.stats = stats; self.last = None
        def execute(self, sql, binds=None, **kw):
            self.sql.append(sql); self.last = sql
        def fetchone(self):
            return (self.stats,) if 'num_rows' in self.last else None

    class Conn:
        def __init__(self, stats):
            self.c = Cursor(stats)
        def cursor(self):
            return self.c

    big = Conn(5_000_000)
    assert OracleCatalog(big).order_ref_exists('PK/26-27/S/1', 'pk/26-27/s/1') is False
    assert 'DOCID IN' in big.c.sql[0] and not any('upper(trim(DOCID))' in s for s in big.c.sql)
    small = Conn(1000)
    assert OracleCatalog(small).order_ref_exists('PK/26-27/S/1') is False
    assert any('upper(trim(DOCID))' in s for s in small.c.sql)          # small tables keep the case-insensitive check
