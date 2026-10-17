"""Evidence ledger (REA's rules): immutable, idempotent, conflicts refused, atomic bundles, open questions."""
import pytest
from fastapi.testclient import TestClient
from backend import evidence, local_store, main
from backend.erp import mapping as registry


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    local_store.init(); evidence.init()


def rec(**k):
    return dict(dict(subject='column:ORDMAS.BONO', kind='business_decision', source='reviewer:asha',
                     claim='Axpert Order Entry → Buyer PO field fills BONO'), **k)


def test_same_evidence_twice_is_stored_once(ledger):
    a = evidence.record([rec()], 'asha'); b = evidence.record([rec()], 'asha')
    assert a[0]['id'] == b[0]['id'] and len(evidence.for_subject('column:ORDMAS.BONO')) == 1


def test_conflicting_claim_from_same_source_is_refused_until_superseded(ledger):
    first = evidence.record([rec()], 'asha')[0]
    with pytest.raises(evidence.EvidenceError, match='already recorded different'):
        evidence.record([rec(claim='Actually BONO is the buyer order date field')], 'asha')
    evidence.record([rec(claim='Actually BONO is the buyer PO number (checked order 1320)', supersedes=first['id'])], 'asha')
    current = evidence.for_subject('column:ORDMAS.BONO')
    assert [r['claim'][:8] for r in current] == ['Actually'] and len(evidence.for_subject('column:ORDMAS.BONO', include_superseded=True)) == 2


def test_bundle_is_all_or_nothing(ledger):
    with pytest.raises(evidence.EvidenceError, match='Unknown evidence kind'):
        evidence.record([rec(), rec(subject='column:ORDMAS.DOCID', kind='looks_similar')], 'asha')
    assert evidence.for_subject('column:ORDMAS.BONO') == []


def test_short_statement_and_unknown_subject_are_refused(ledger):
    with pytest.raises(evidence.EvidenceError, match='at least'):
        evidence.record([rec(claim='looks right')], 'asha')
    with pytest.raises(evidence.EvidenceError, match='subject'):
        evidence.record([rec(subject='ORDMAS.BONO; drop table')], 'asha')


def test_questions_close_only_with_evidence_for_the_same_subject(ledger):
    q = evidence.ask('join:ORDSIZE->ORDSTYLE:SUB3_SUBNO=SUBNO', 'Is SUBNO unique within one order?', 'asha')
    other = evidence.record([rec()], 'asha')[0]
    with pytest.raises(evidence.EvidenceError, match='same subject'):
        evidence.answer(q, other['id'], 'asha')
    ans = evidence.record([dict(subject='join:ORDSIZE->ORDSTYLE:SUB3_SUBNO=SUBNO', kind='verified_sql', source='reviewer:asha',
                                claim='0 duplicate SUBNO per ORDMASID in 4,210 orders')], 'asha')[0]
    evidence.answer(q, ans['id'], 'asha')
    assert evidence.questions() == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as c:
        c.post('/api/setup', json={'username': 'asha', 'password': 'test-password-123'})
        yield c


def test_verifying_a_mapping_needs_a_statement_and_records_the_known_facts(client):
    m = registry.effective()
    table, col = next((t, c) for t, spec in m['tables'].items() for c, v in spec['columns'].items()
                      if not v.get('key') and 'parent' not in v and v.get('evidence'))
    r = client.post('/api/mapping/status', json={'table': table, 'column': col, 'status': 'verified'})
    assert r.status_code == 409 and 'at least' in r.json()['detail']
    r = client.post('/api/mapping/status', json={'table': table, 'column': col, 'status': 'verified',
                                                 'evidence': 'Checked in Axpert Order Entry: this field is saved here'})
    assert r.status_code == 200, r.text
    kinds = {x['kind'] for x in client.get('/api/evidence', params={'subject': f'column:{table}.{col}'}).json()['records']}
    assert kinds == {'business_decision', 'erp_screen'}
