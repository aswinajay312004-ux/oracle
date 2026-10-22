"""Data Expert Agent. The deterministic steps run on real workbooks. The local-AI step is tested for its SAFETY
logic with a stand-in model answer (no real model runs in this test): only offered columns and cells are accepted,
values must fit the column type, and a missing model is skipped cleanly."""
import json
import pytest
from backend import agent, workbooks
from tests import factory
from tests.test_order_table import cols


def test_deterministic_steps_and_sections():
    draft = dict(workbooks.parse(factory.merch_bom()), id='x', version=1)
    rep = agent.run(draft)
    keys = [s['key'] for s in rep['steps']]
    assert keys == ['read', 'layout', 'json', 'sections', 'match', 'ai']
    assert rep['steps'][1]['detail'] == 'Merch BOM'
    names = {s['name']: s for s in rep['sections']}
    assert names['Order quantities']['status'] == 'saved' and names['Sample quantities']['status'] == 'empty'
    assert names['Fabric Requirement']['kind'] == 'fabric' and names['Fabric Requirement']['status'] == 'reference'
    assert names['Care Label']['kind'] == 'trims'
    assert names['Printing']['status'] == 'not_provided'  # absent is not assumed to be "no"


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


def test_ai_proposals_are_validated(monkeypatch):
    draft = dict(workbooks.parse(factory.merch_bom(extra_header=True)), id='x', version=1)
    c = {name: dict(meta, system=False, labels=[meta['label']]) for name, meta in cols().items()}
    table = 'EXCEL_ORDERS'
    plan = dict(table=table, orders=[dict(tables={table: dict(boxes={
        'REMARKS': dict(state='blank'), 'PRICE': dict(state='blank')})})], unused=[
        dict(sheet='ST1', cell='H3', label='Payment Terms', value='LC 60 DAYS'),
        dict(sheet='ST1', cell='H2', label='Ship Mode', value='SEA')])
    monkeypatch.setattr(agent, 'available', lambda model=None: ('qwen2.5:7b', ['qwen2.5:7b']))
    seen = {}
    def fake_post(url, timeout, json):
        seen['schema'] = json['format']
        answer = {'mappings': [
            {'column': 'REMARKS', 'cell': 'ST1!H3', 'reason': 'payment terms note'},      # valid: offered column + cell
            {'column': 'PRICE', 'cell': 'ST1!H2', 'reason': 'wrong'},                      # SEA is not a number
            {'column': 'BUYERNAME', 'cell': 'ST1!Z99', 'reason': 'made up'}]}              # not offered
        return FakeResponse({'message': {'content': __import__('json').dumps(answer)}})
    monkeypatch.setattr(agent.httpx, 'post', fake_post)
    rep = agent.ai_review(draft, plan, c)
    enum_cols = seen['schema']['properties']['mappings']['items']['properties']['column']['enum']
    assert 'REMARKS' in enum_cols and 'BUYERNAME' not in enum_cols  # only still-empty columns are offered
    accepted = [p for p in rep['proposals'] if p['accepted']]
    assert [(p['column'], p['value']) for p in accepted] == [('REMARKS', 'LC 60 DAYS')]
    binding = draft['orders'][0]['bind'][table]['REMARKS']
    assert binding['cell'] == 'H3' and binding['via'].startswith('AI suggestion')
    assert len(rep['proposals']) == 3 and sum(not p['accepted'] for p in rep['proposals']) == 2


def test_ai_skips_cleanly_without_a_model(monkeypatch):
    draft = dict(workbooks.parse(factory.merch_bom(extra_header=True)), id='x', version=1)
    monkeypatch.setattr(agent, 'available', lambda model=None: (None, []))
    c = {name: dict(meta, system=False, labels=[meta['label']]) for name, meta in cols().items()}
    plan = dict(table='EXCEL_ORDERS', orders=[dict(tables={'EXCEL_ORDERS': dict(boxes={'REMARKS': dict(state='blank')})})],
                unused=[dict(sheet='ST1', cell='H3', label='Payment Terms', value='LC 60 DAYS')])
    rep = agent.ai_review(draft, plan, c)
    assert rep['status'] == 'skip' and 'not running' in rep['detail'] and 'bind' not in draft['orders'][0]


def test_ask_keyword_reader_picks_intent_and_real_filters():
    from backend import ask
    assert ask.keyword_intent("how many pieces for carter's?", False, "CARTER'S", None) == 'total_pieces'
    assert ask.keyword_intent('how many orders this month', False, None, None) == 'order_count'
    assert ask.keyword_intent('size wise quantity of CT27085', True, None, None) == 'order_sizes'
    assert ask.keyword_intent('which uploads are waiting for approval', False, None, None) == 'pending'
    assert ask.keyword_intent('pieces per buyer', False, None, None) == 'by_buyer'


@pytest.mark.parametrize('question', ['how many orders this month', 'pieces for NEXT last year', 'orders in the past 30 days'])
def test_ask_refuses_time_filter_it_cannot_apply(question):
    from backend import ask
    result = ask.answer(None, 'EXCEL_ORDERS', question)
    assert result['intent'] == 'unsupported_filter'
    assert result['sql'] == [] and 'cannot apply' in result['text']


@pytest.mark.skipif(__import__('os').getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 for local Oracle tests')
def test_agent_ai_endpoint_applies_only_validated_proposals(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import local_store, main
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setenv('POPPYS_ORDER_TARGET', 'table')
    monkeypatch.setattr(agent, 'available', lambda model=None: ('qwen2.5:7b-instruct', ['qwen2.5:7b-instruct']))
    answer = {'mappings': [{'column': 'REMARKS', 'cell': 'ST1!H3', 'reason': 'terms'}]}
    monkeypatch.setattr(agent.httpx, 'post', lambda url, timeout, json: FakeResponse({'message': {'content': __import__('json').dumps(answer)}}))
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'})
        d = c.post('/api/uploads', files={'file': ('b.xlsx', factory.merch_bom(extra_header=True))}).json()
        assert d['agent']['steps'][-1] == {**d['agent']['steps'][-1], 'key': 'ai', 'status': 'pending'}
        r = c.post(f"/api/drafts/{d['id']}/agent/ai", json={'version': d['version']})
        assert r.status_code == 200, r.text
        got = r.json()
        # Payment Terms is already mapped deterministically to PT. The model must not
        # invent an extra REMARKS mapping when there is no unused candidate cell.
        assert got['agent']['ai']['proposals'] == []
        p = c.post(f"/api/drafts/{d['id']}/preview", json={'version': got['version']}).json()['plan']
        assert p['operations'][0]['values']['PT'] == 'LC 60 DAYS'
        assert 'REMARKS' not in p['operations'][0]['values']
