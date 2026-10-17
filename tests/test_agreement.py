"""Two readings of a weak question (rules and local AI), compared by running both (XiYan-SQL-style candidate
selection), and the M-Schema catalog cards the local model sees."""
from datetime import date
import json
import pytest
from backend.erp import mapping as registry
from backend.insight import engine, service, understand, knowledge as K
from backend.insight.query import Dialect
from backend.insight.understand import lexicon
import demo_db


@pytest.fixture
def w(monkeypatch):
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite'); idx = {}
    for t, c in service.indexable(kb):
        for v, n in ex(f'SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}', {}, 5000):
            idx.setdefault(K.norm(v), []).append(dict(table=t, column=c, value=str(v), n=n))
    real = engine.interpret

    def weak(*a, **k):   # the rules understood the question, but not confidently
        intent, conf = real(*a, **k)
        return intent, dict(conf, score=0.5)
    monkeypatch.setattr(engine, 'interpret', weak)
    lex = lexicon(kb)
    prompts = []

    def ask(q, reply):
        def chat(messages):
            prompts.append(messages[1]['content'])
            return json.dumps(reply)
        return engine.ask(q, kb, {'db': (ex, d), 'local': (ex, d)}, finder=lambda p: idx.get(K.norm(p), []), today=date(2026, 10, 17),
                          lex=lex, ai_chat=chat, samples=lambda t, c: [v for v, in conn.execute(f'SELECT DISTINCT {c} FROM {t} LIMIT 3')])
    return dict(ask=ask, prompts=prompts, kb=kb)


def test_same_reading_raises_confidence(w):
    r = w['ask']('How many orders for NEXT?', dict(base='ORDMAS', measures=[dict(agg='count')],
                                                   filters=[dict(column='BUYMAS.BUYERNAME', op='eq', value='NEXT')]))
    assert r['rows'] == [[2]] and r['source'] == 'rules' and r['confidence']['score'] >= 0.8
    assert any('same way' in x for x in r['confidence']['reasons'])


def test_different_reading_with_the_same_result_keeps_the_rules_answer(w):
    # count of orders vs. count of distinct order references: different plans, same figure
    r = w['ask']('How many orders for NEXT?', dict(base='ORDMAS', measures=[dict(agg='count', column='ORDMAS.DOCID')],
                                                   filters=[dict(column='BUYMAS.BUYERNAME', op='eq', value='NEXT')]))
    assert r['source'] == 'rules' and r['rows'] == [[2]] and r['confidence']['level'] == 'high'
    assert any('same result' in x for x in r['confidence']['reasons']) and not r.get('alternatives')


def test_different_result_offers_the_other_reading(w):
    r = w['ask']('How many orders for NEXT?', dict(base='ORDMAS', measures=[dict(agg='count')]))   # the AI dropped the buyer
    assert r['source'].startswith('local AI') and r['rows'] == [[3]] and r['confidence']['level'] != 'high'
    alt = r['alternatives'][0]
    assert alt['source'] == 'question rules' and 'NEXT' in alt['understood'] and alt['intent']['filters']


def test_an_unusable_ai_reading_falls_back_to_the_rules(w):
    r = w['ask']('How many orders for NEXT?', dict(base='ORDMAS', measures=[dict(agg='sum', column='ORDMAS.NOT_A_COLUMN')]))
    assert r['source'] == 'rules' and r['rows'] == [[2]] and not r.get('alternatives')


def test_the_model_sees_m_schema_cards_with_keys_examples_and_links(w):
    w['ask']('How many orders for NEXT?', dict(base='ORDMAS', measures=[dict(agg='count')]))
    prompt = w['prompts'][0]
    assert prompt.startswith('【Schema】') and '# Table: ORDMAS, Order' in prompt
    assert '(ORDMASID:NUMBER, ' in prompt and 'Primary Key' in prompt
    assert 'Examples: [' in prompt and '【Foreign keys】' in prompt and 'ORDMAS.BUYER=BUYMAS.BUYMASID' in prompt
    cards = understand.schema_cards(w['kb'], ['ORDMAS'])
    assert 'Examples' not in cards and '【Foreign keys】' not in cards     # no samples given, no second table
