"""Patient isolation, human publication, staged uploads, and fresh approval."""
import base64
import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.main import create_app
from backend.db.db import connect
from backend.schemas import Case


def sign_in(client, username='officer@claimpilot.demo', password='OfficerDemo!2026'):
    result = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert result.status_code == 200, result.text
    return result


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAIMPILOT_LLM', 'off')
    monkeypatch.setenv('CLAIMPILOT_UPLOADS', str(tmp_path / 'uploads'))
    app = create_app(tmp_path / 'claims.db')
    with TestClient(app) as staff, TestClient(app) as patient, TestClient(app) as other:
        sign_in(staff)
        sign_in(patient, 'ramesh@claimpilot.demo', 'PatientDemo!2026')
        sign_in(other, 'patient1@claimpilot.demo', 'PatientDemo!2026')
        yield staff, patient, other


def start(staff, case='case2'):
    result = staff.post('/api/cases/start', json={'case_id': case})
    assert result.status_code == 202, result.text
    claim_id = result.json()['claim_id']
    staff.app.state.runner.wait(claim_id)
    return claim_id


def pdf_body(text='Finding: Severe right knee osteoarthritis. Joint space narrowing supports knee replacement.'):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), 'Synthetic X-ray report\nDetails\n' + text)
        content = pdf.tobytes()
    return {'type': 'xray_report', 'filename': 'synthetic-xray.pdf', 'content_base64': base64.b64encode(content).decode()}


def test_role_boundaries_and_logout(clients):
    staff, patient, other = clients
    for path in ['/api/claims', '/api/cases', '/api/queue', '/api/dashboard', '/api/ready']:
        assert patient.get(path).status_code == 403
    assert staff.get('/api/portal/dashboard').status_code == 403
    assert patient.post('/api/cases/start', json={'case_id': 'case1'}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with patient.websocket_connect('/ws/trace'):
            pass
    assert patient.post('/api/auth/logout').status_code == 200
    assert patient.get('/api/portal/dashboard').status_code == 401
    assert patient.get('/api/claims').status_code == 401
    assert patient.post('/api/auth/login', json={'username': 'ramesh@claimpilot.demo', 'password': 'wrong'}).status_code == 401


def test_policy_profile_and_publication_gate(clients):
    staff, patient, other = clients
    claim_id = start(staff)
    dashboard = patient.get('/api/portal/dashboard').json()
    policy = dashboard['profiles'][0]['policy']
    assert policy['sum_insured'] == 300000
    assert policy['expiry_date'] == '2027-03-31'
    assert policy['product'] == 'Health Secure Individual'
    assert dashboard['claims'][0]['latest'] is None
    assert other.get('/api/portal/dashboard').json()['claims'] == []
    preview = staff.get(f'/api/patient-desk/{claim_id}').json()
    assert patient.get(f'/api/patient-desk/{claim_id}').status_code == 403
    assert staff.post(f'/api/patient-desk/{claim_id}/publish', json={'snapshot_hash': 'stale'}).status_code == 409
    response = staff.post(f'/api/patient-desk/{claim_id}/publish', json={'snapshot_hash': preview['snapshot_hash']})
    assert response.status_code == 200
    latest = patient.get('/api/portal/dashboard').json()['claims'][0]['latest']
    assert latest['estimate']['insurer'] == 185800
    assert latest['approved_by'] == 'Billing Officer'
    assert not any(x in json.dumps(latest) for x in ['fraud', 'risk_score', 'file_path', 'prompt'])
    assert staff.post(f'/api/patient-desk/{claim_id}/publish', json={'snapshot_hash': preview['snapshot_hash']}).json()['duplicate']
    assert len(patient.get('/api/portal/dashboard').json()['claims'][0]['history']) == 1


def test_upload_stays_pending_until_officer_accepts_then_rechecks(clients):
    staff, patient, other = clients
    claim_id = start(staff)
    before = staff.get(f'/api/claims/{claim_id}').json()
    request = staff.post(f'/api/patient-desk/{claim_id}/requests', json={'type': 'xray_report', 'message': 'Please send your synthetic X-ray report.'})
    assert request.status_code == 201
    body = pdf_body() | {'request_id': request.json()['request_id']}
    assert other.post(f'/api/portal/claims/{claim_id}/uploads', json=body).status_code == 404
    response = patient.post(f'/api/portal/claims/{claim_id}/uploads', json=body)
    assert response.status_code == 201, response.text
    upload_id = response.json()['upload_id']
    assert patient.post(f'/api/portal/claims/{claim_id}/uploads', json=body).json()['duplicate']
    assert len(staff.get(f'/api/claims/{claim_id}').json()['case']['documents']) == len(before['case']['documents'])
    file_path = f'/api/portal/claims/{claim_id}/uploads/{upload_id}/file'
    assert other.get(file_path).status_code == 404
    assert patient.get(file_path).status_code == 200
    assert staff.get(file_path).status_code == 200
    route = f'/api/patient-desk/{claim_id}/uploads/{upload_id}/review'
    assert patient.post(route, json={'accept': True, 'reason': 'Approve me'}).status_code == 403
    response = staff.post(route, json={'accept': True, 'reason': 'Reviewed the synthetic report; matches the claim.'})
    assert response.status_code == 200, response.text
    staff.app.state.runner.wait(claim_id)
    after = staff.get(f'/api/claims/{claim_id}').json()
    assert len(after['case']['documents']) == len(before['case']['documents']) + 1
    assert after['state'] == 'AWAITING_HUMAN'
    assert after['pending_draft']['version'] > before['pending_draft']['version']
    assert after['thread'] == []  # acceptance is not permission to submit to insurer
    result = patient.get('/api/portal/dashboard').json()['claims'][0]
    assert result['requests'][0]['status'] == 'fulfilled'
    assert result['uploads'][0]['status'] == 'accepted'
    assert result['latest'] is None  # no automatic patient publication
    assert staff.post(route, json={'accept': True, 'reason': 'Repeat'}).status_code == 409


def test_rejected_and_malformed_uploads(clients):
    staff, patient, other = clients
    claim_id = start(staff)
    bad = pdf_body() | {'content_base64': base64.b64encode(b'not a PDF').decode()}
    assert patient.post(f'/api/portal/claims/{claim_id}/uploads', json=bad).status_code == 422
    bad['content_base64'] = '!!!!!!!!'
    assert patient.post(f'/api/portal/claims/{claim_id}/uploads', json=bad).status_code == 422
    upload = patient.post(f'/api/portal/claims/{claim_id}/uploads', json=pdf_body()).json()
    route = f"/api/patient-desk/{claim_id}/uploads/{upload['upload_id']}/review"
    assert staff.post(route, json={'accept': False, 'reason': 'Please upload the correct side of the report.'}).status_code == 200
    result = patient.get('/api/portal/dashboard').json()['claims'][0]['uploads'][0]
    assert result['status'] == 'rejected' and 'correct side' in result['reason']


def test_session_expiry_csrf_and_database_passwords(clients):
    staff, patient, other = clients
    assert patient.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert patient.post('/api/auth/login', headers={'Origin': 'http://127.0.0.1:5173'}, json={'username': 'ramesh@claimpilot.demo', 'password': 'PatientDemo!2026'}).status_code == 200
    with connect(staff.app.state.db_path) as conn:
        assert all('Demo!2026' not in r[0] for r in conn.execute('SELECT password_hash FROM portal_accounts'))
        conn.execute('UPDATE portal_sessions SET expires_at=0')
    assert patient.get('/api/portal/dashboard').status_code == 401


def test_invalid_policy_dates_are_rejected():
    case = json.loads(Path('fixtures/case2.json').read_text())
    case['policy']['expiry_date'] = '2020-01-01'
    with pytest.raises(ValueError, match='expiry'):
        Case.model_validate(case)
