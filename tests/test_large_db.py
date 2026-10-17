"""Large-database behaviour (docs/PERFORMANCE.md): index-friendly live lookups with a time budget, a value
index that keeps the newest values of big columns, time-limited counts, explorer filters that an index can
answer, plain-English timeouts and sampled discovery. The fast tests use SQLite and fakes; the last section
runs on the local Oracle when POPPYS_TEST_ORACLE=1."""
from contextlib import contextmanager
import os
import time
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from backend.erp import mapping as registry
from backend.insight import service
from backend.insight.query import Dialect, QueryError
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
def test_lookup_plan_puts_cheap_lookups_first_and_skips_fully_indexed_big_columns():
    small, indexed, scan = ({(t, c) for t, c, _ in tier} for tier in service.lookup_plan(big_kb(), complete={'ORDMAS.SAMPLETYPE'}))
    assert ('BUYMAS', 'BUYERNAME') in small and ('STYLENUM', 'STYLENUMBER') in small
    assert indexed == {('ORDMAS', 'DOCID')}
    assert ('ORDMAS', 'BONO') in scan and ('ORDMAS', 'SAMPLETYPE') not in scan | indexed | small
    unknown = service.lookup_plan(big_kb(indexed=None), complete=set())   # harvested before index info: try every big column as indexed
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
    (tmp_path / service.VALUE_META).write_text('{"complete": ["ORDMAS.SAMPLETYPE", "ORDMAS.ORDCATEGORY"]}', encoding='utf-8')
    spy = Spy(answer=lambda *a: [])
    assert service.live_finder(big_kb(), spy, ORACLE)('PK/26-27/S/9999') == []
    probed = {c for s in spy.sql if on(s, 'ORDMAS', 'ORDSTYLE') for c in ('DOCID', 'BONO', 'SAMPLETYPE', 'ORDCATEGORY', 'COMBO') if f'WHERE {c} ' in s}
    assert probed == {'DOCID', 'BONO'}
    spy.sql.clear(); service.live_finder(big_kb(), spy, ORACLE)('X' * 40 + '1')   # longer than DOCID (30): not looked up there
    assert not any('WHERE DOCID ' in s for s in spy.sql) and any('WHERE BONO ' in s for s in spy.sql)


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
    complete = service.complete_values()
    assert 'ORDMAS.SAMPLETYPE' in complete and 'BUYMAS.BUYERNAME' in complete and 'ORDMAS.DOCID' not in complete


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
    assert r.status_code == 409 and '“is exactly”' in r.json()['detail'] and '24,000,000' in r.json()['detail']
    assert c.get('/api/explorer/tables/ORDSIZE', params=dict(after='abc')).status_code == 422


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
