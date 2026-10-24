"""The assistant: grounded, cited, verified answers; judgement questions go to a person."""

import pytest
from fastapi.testclient import TestClient

from backend import assistant, llm
from backend.api.main import create_app
from backend.assistant import Source, inr, verify


def sign_in(client, username='officer@claimpilot.demo', password='OfficerDemo!2026'):
    assert client.post('/api/auth/login', json={'username': username, 'password': password}).status_code == 200


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAIMPILOT_LLM', 'off')  # answers come from the sources' own words
    monkeypatch.setenv('CLAIMPILOT_UPLOADS', str(tmp_path / 'uploads'))
    app = create_app(tmp_path / 'claims.db')
    with TestClient(app) as staff, TestClient(app) as patient, TestClient(app) as other:
        sign_in(staff)
        sign_in(patient, 'ramesh@claimpilot.demo', 'PatientDemo!2026')
        sign_in(other, 'patient1@claimpilot.demo', 'PatientDemo!2026')
        yield staff, patient, other


def start(staff, case='case2'):
    claim_id = staff.post('/api/cases/start', json={'case_id': case}).json()['claim_id']
    staff.app.state.runner.wait(claim_id)
    return claim_id


def publish(staff, claim_id):
    preview = staff.get(f'/api/patient-desk/{claim_id}').json()
    assert staff.post(f'/api/patient-desk/{claim_id}/publish', json={'snapshot_hash': preview['snapshot_hash']}).status_code == 200


def ask(client, question, claim_id=None, path='/api/portal/assistant/ask'):
    response = client.post(path, json={'question': question, 'claim_id': claim_id})
    assert response.status_code == 200, response.text
    return response.json()


# ---- The verifier ------------------------------------------------------------------------------


def test_verifier_rejects_unbacked_numbers_missing_citations_and_promises():
    sources = [Source('S1', 'policy', 'Your policy, clause 4.2', 'Room rent is payable up to 1% of the sum insured per day.')]
    assert verify('Room rent is capped at 1% of the sum insured a day [S1].', sources) == []
    assert 'states numbers no source contains: 3' in verify('Capped at 3% [S1].', sources)
    assert 'cites no source' in verify('Room rent is capped.', sources)
    assert any('exist' in p for p in verify('Capped at 1% [S2].', sources))
    assert 'promises an outcome' in verify('Your claim will be approved [S1].', sources)
    assert inr(240000) == '₹2,40,000' and inr(185800) == '₹1,85,800'


def test_a_model_answer_that_fails_the_check_is_never_shown(monkeypatch, tmp_path):
    monkeypatch.delenv('CLAIMPILOT_LLM', raising=False)
    monkeypatch.setattr(llm, 'complete', lambda *a, **k: 'Your room limit is ₹9,999 a day and it will be approved [S1].')
    sources = [Source('S1', 'policy', 'Your policy, clause 4.2', 'Room rent is payable up to 1% of the sum insured per day.')]
    result = assistant._finish('policy', sources, 'Your policy, clause 4.2 says: “Room rent is payable up to 1% of the sum insured per day.” [S1]',
                               'room limit?', system='')
    assert result['method'] == 'extractive' and result['verified']
    assert '9,999' not in result['answer']
    assert any('Model answer rejected' in c for c in result['checks'])


# ---- Patients ----------------------------------------------------------------------------------


def test_patient_policy_question_is_answered_from_their_own_clauses(clients):
    staff, patient, _ = clients
    start(staff)
    result = ask(patient, 'Is there a limit on room rent in my policy?')
    assert result['verified'] and result['intent'] == 'policy'
    own = [s for s in result['sources'] if s['kind'] == 'policy']
    assert own and own[0]['label'] == 'Your policy, clause 4.2'
    assert '[S1]' in result['answer']


def test_patient_sees_no_figures_until_an_officer_publishes(clients):
    staff, patient, _ = clients
    claim_id = start(staff)
    before = ask(patient, 'How much will I have to pay?')
    assert before['sources'] == [] and '₹' not in before['answer']
    publish(staff, claim_id)
    after = ask(patient, 'How much will I have to pay?')
    assert after['verified'] and after['intent'] == 'money'
    assert after['sources'][0]['kind'] == 'claim' and '₹' in after['answer']
    assert 'not a payment guarantee' in after['answer']


def test_judgement_questions_go_to_the_officer_and_their_reply_comes_back(clients):
    staff, patient, _ = clients
    claim_id = start(staff)
    result = ask(patient, 'Why was my claim cut? Can you appeal it?')
    assert result['intent'] == 'handoff' and result['handoff']['status'] == 'waiting'
    question_id = result['handoff']['question_id']

    queue = staff.get(f'/api/patient-desk/{claim_id}/questions').json()
    assert queue[0]['question_id'] == question_id and queue[0]['answer'] is None
    reply = 'Your room was above the policy limit; we are checking whether a cheaper room is possible.'
    assert staff.post(f'/api/patient-desk/{claim_id}/questions/{question_id}/answer', json={'answer': reply}).status_code == 200
    assert staff.post(f'/api/patient-desk/{claim_id}/questions/{question_id}/answer', json={'answer': reply}).status_code == 409

    history = patient.get('/api/portal/assistant/history').json()['history']
    item = next(h for h in history if (h.get('handoff') or {}).get('question_id') == question_id)
    assert item['handoff']['status'] == 'answered' and item['handoff']['reply']['answer'] == reply


def test_patients_cannot_reach_another_claim_or_officer_tools(clients):
    staff, patient, other = clients
    claim_id = start(staff)
    assert other.post('/api/portal/assistant/ask', json={'question': 'room limit?', 'claim_id': claim_id}).status_code == 404
    assert patient.post('/api/assistant/ask', json={'question': 'which claims are waiting?'}).status_code == 403
    assert patient.get(f'/api/patient-desk/{claim_id}/questions').status_code == 403


def test_glossary_terms_are_explained(clients):
    _, patient, _ = clients
    result = ask(patient, 'What is a co-pay?')
    assert result['verified'] and any(s['kind'] == 'glossary' for s in result['sources'])


# ---- Officers ----------------------------------------------------------------------------------


def test_officer_copilot_explains_risk_with_sources(clients):
    staff, _, _ = clients
    claim_id = start(staff)
    result = ask(staff, 'What should I fix before I send this?', claim_id, path='/api/assistant/ask')
    assert result['verified']
    risk = [s['text'] for s in result['sources'] if s['label'].startswith('Risk checker')]
    assert any('x-ray' in text.lower() or 'xray' in text.lower() for text in risk)


def test_officer_copilot_answers_different_questions_from_different_sources(clients):
    staff, _, _ = clients
    claim_id = start(staff)
    fix = ask(staff, 'What should I fix before I send this?', claim_id, path='/api/assistant/ask')
    room = ask(staff, 'What does the policy say about room rent?', claim_id, path='/api/assistant/ask')
    cost = ask(staff, 'What is the typical bill and length of stay?', claim_id, path='/api/assistant/ask')
    assert 'room' in room['sources'][0]['text'].lower()
    assert any(s['label'] == 'Policy, clause 4.2' for s in room['sources'])
    assert cost['sources'][0]['label'].startswith('Cost estimator')
    assert len({fix['answer'], room['answer'], cost['answer']}) == 3


def test_officer_copilot_summarises_the_desk(clients):
    staff, _, _ = clients
    start(staff)
    start(staff, 'case1')
    result = ask(staff, 'How many claims are waiting for me?', path='/api/assistant/ask')
    assert result['verified'] and result['intent'] == 'desk'
    assert 'Total 2 claims' in result['sources'][0]['text']


def test_officer_copilot_says_so_when_nothing_matches(clients):
    staff, _, _ = clients
    claim_id = start(staff)
    result = ask(staff, 'Who won the cricket world cup in 1983?', claim_id, path='/api/assistant/ask')
    assert result['sources'] == [] and "Nothing in this claim's record" in result['answer']
