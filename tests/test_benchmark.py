"""Ask AI accuracy benchmark (backend/insight/benchmark.py): golden questions with hand-written SQL, run against
the SQLite copy of the GARMENT_DEMO structure. Every question must be answered correctly; a new failure here
means a change made Ask AI less accurate."""
from datetime import date
from pathlib import Path
import pytest
from backend.erp import mapping as registry
from backend.insight import benchmark, engine, service, knowledge as K
from backend.insight.query import Dialect
from backend.insight.understand import lexicon
import demo_db

GOLDEN = Path(__file__).parent / 'golden' / 'demo.json'
CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'golden_questions.json'


@pytest.fixture(scope='module')
def bench():
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite'); idx = {}
    for t, c in service.indexable(kb):
        for v, n in ex(f'SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}', {}, 5000):
            idx.setdefault(K.norm(v), []).append(dict(table=t, column=c, value=str(v), n=n))
    lex = lexicon(kb)

    def ask(q):
        return engine.ask(q, kb, {'db': (ex, d), 'local': (ex, d)}, finder=lambda p: idx.get(K.norm(p), []), today=date(2026, 10, 17), lex=lex)
    return dict(ask=ask, ex=ex, d=d, conn=conn)


def test_every_golden_demo_question_is_answered_correctly(bench):
    report = benchmark.run(benchmark.load(GOLDEN), bench['ask'], bench['ex'], bench['d'])
    wrong = [(i['id'], i['status'], i['detail']) for i in report['items'] if i['status'] != 'correct']
    assert not wrong and report['accuracy'] == 1.0, wrong
    sizes = next(i for i in report['items'] if i['id'] == 'newest-order-sizes')
    assert sizes['question'] == 'size wise quantity for order PK/26-27/S/9003'      # param picked from the data


def test_the_shipped_golden_set_runs_on_any_copy_of_the_erp(bench):
    """config/golden_questions.json names no buyer or order: its values are picked from the database."""
    report = benchmark.run(benchmark.load(CONFIG), bench['ask'], bench['ex'], bench['d'])
    assert report['total'] >= 8 and report['by_status']['error'] == 0, report['items']
    assert report['accuracy'] == 1.0, [(i['id'], i['detail']) for i in report['items'] if i['status'] != 'correct']


def test_lines_of_cancelled_orders_are_not_counted(bench):
    """Found by the benchmark: size and line tables have no cancel flag of their own, so their order's decides."""
    r = bench['ask']('size wise quantity')
    assert "CANCEL <> 'T'" in r['sql'] and any('cancelled orders are excluded' in n for n in r['notes'])
    cancelled = bench['ask']('size wise quantity of cancelled orders')
    assert sum(row[1] for row in cancelled['rows']) == 1                              # only order 104's single piece


def test_a_wrong_answer_is_reported_with_both_results(bench):
    q = [dict(id='x', question='How many orders?', sql='SELECT 99 FROM {ORDMAS}', compare='scalar')]
    report = benchmark.run(q, bench['ask'], bench['ex'], bench['d'])
    assert report['items'][0]['status'] == 'wrong' and 'expected 99, got 3' in report['items'][0]['detail']
    assert report['accuracy'] == 0


def test_hand_written_sql_also_passes_the_safety_review(bench):
    q = [dict(id='x', question='How many orders?', sql='DELETE FROM {ORDMAS}', compare='scalar')]
    item = benchmark.run(q, bench['ask'], bench['ex'], bench['d'])['items'][0]
    assert item['status'] == 'error' and 'SELECT' in item['detail']
    assert bench['conn'].execute('SELECT COUNT(*) FROM ORDMAS').fetchone()[0] == 4


def test_golden_files_are_validated():
    with pytest.raises(ValueError, match='compare'):
        benchmark.load_items([dict(question='q', sql='SELECT 1', compare='nearly')], 'x.json')
