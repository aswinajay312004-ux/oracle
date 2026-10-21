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
    rep = agent.run(draft, cols())
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
    c = cols()
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
    rep = agent.ai_review(draft, c)
    enum_cols = seen['schema']['properties']['mappings']['items']['properties']['column']['enum']
    assert 'REMARKS' in enum_cols and 'BUYERNAME' not in enum_cols  # only still-empty columns are offered
    accepted = [p for p in rep['proposals'] if p['accepted']]
    assert [(p['column'], p['value']) for p in accepted] == [('REMARKS', 'LC 60 DAYS')]
    tv = draft['orders'][0]['table_values']['REMARKS']
    assert tv['source']['cell'] == 'H3' and tv['source']['via'].startswith('AI suggestion')
    assert len(rep['proposals']) == 3 and sum(not p['accepted'] for p in rep['proposals']) == 2


def test_ai_skips_cleanly_without_a_model(monkeypatch):
    draft = dict(workbooks.parse(factory.merch_bom(extra_header=True)), id='x', version=1)
    monkeypatch.setattr(agent, 'available', lambda model=None: (None, []))
    rep = agent.ai_review(draft, cols())
    assert rep['status'] == 'skip' and 'not running' in rep['detail'] and 'table_values' not in draft['orders'][0]


def test_ask_keyword_reader_picks_intent_and_real_filters():
    from backend import ask
    buyers, styles, refs = ["CARTER'S", 'NEXT'], ['CT27085'], ['TEST-CT27085-01']
    assert ask.keyword_intent("how many pieces for carter's?", buyers, styles, refs) == dict(intent='total_pieces', buyer="CARTER'S", style=None, oc=None)
    assert ask.keyword_intent('how many orders this month', buyers, styles, refs)['intent'] == 'order_count'
    assert ask.keyword_intent('size wise quantity of CT27085', buyers, styles, refs) == dict(intent='by_size', buyer=None, style='CT27085', oc=None)
    assert ask.keyword_intent('which uploads are waiting for approval', buyers, styles, refs)['intent'] == 'pending'
    assert ask.keyword_intent('pieces per buyer', buyers, styles, refs)['intent'] == 'by_buyer'


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
        assert got['agent']['ai']['proposals'][0]['accepted'] and got['orders'][0]['table_values']['REMARKS']['value'] == 'LC 60 DAYS'
        p = c.post(f"/api/drafts/{d['id']}/preview", json={'version': got['version']}).json()['plan']
        assert p['operations'][0]['values']['REMARKS'] == 'LC 60 DAYS'
        assert p['operations'][0]['columns']['REMARKS']['source']['via'].startswith('AI suggestion')
