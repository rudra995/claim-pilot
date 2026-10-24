"""Patient-safe snapshots, pending evidence, and officer review. No claim state writes."""
import base64
import binascii
import hashlib
import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend import portal_auth, remediation
from backend.agents import orchestrator
from backend.api import store
from backend.db import audit
from backend.db.db import connect
from backend.rules.engine import assess
from backend.schemas import DocumentType

STATUS = {
    'ADMITTED': 'Hospital preparing your claim', 'PREAUTH_DRAFT': 'Hospital preparing your claim',
    'RISK_CHECK': 'Hospital checking your documents', 'AWAITING_HUMAN': 'Billing officer reviewing',
    'SUBMITTED': 'Sent to insurer', 'QUERY_RAISED': 'Insurer requested information',
    'APPROVED': 'Approval received', 'PARTIALLY_APPROVED': 'Approved with deductions',
    'DENIED': 'Please contact your billing officer', 'IN_TREATMENT': 'Treatment in progress',
    'DISCHARGE_DRAFT': 'Preparing discharge claim', 'FINAL_APPROVED': 'Final approval received',
    'SETTLED': 'Payment received; being reconciled', 'CLOSED': 'Claim closed',
}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=150)


class Publish(BaseModel):
    snapshot_hash: str


class DocumentRequest(BaseModel):
    type: DocumentType
    message: str = Field(min_length=5, max_length=500)


class PatientUpload(BaseModel):
    type: DocumentType
    filename: str = Field(min_length=1, max_length=150)
    content_base64: str = Field(min_length=8, max_length=7_000_000)
    request_id: str | None = None


class Review(BaseModel):
    accept: bool
    reason: str = Field(min_length=3, max_length=500)


def user(role: str | None = None) -> dict:
    account = portal_auth.current_user.get()
    if account is None:
        raise HTTPException(401, 'Please sign in')
    if role and account['role'] != role:
        raise HTTPException(403, f'{role.title()} access required')
    return account


def own_claim(db_path: Path | str, claim_id: str) -> dict:
    account = user()
    with connect(db_path) as conn:
        row = conn.execute('SELECT c.claim_id,c.state,a.patient_id FROM claims c JOIN admissions a USING(admission_id) WHERE claim_id=?', (claim_id,)).fetchone()
    if row is None or (account['role'] == 'patient' and row['patient_id'] != account['patient_id']):
        raise HTTPException(404, 'Claim not found')
    return dict(row)


def snapshot(db_path: Path | str, claim_id: str) -> dict:
    row = own_claim(db_path, claim_id)
    case = store.load(db_path, claim_id)
    assessment = assess(case)
    return {'claim_id': claim_id, 'claim_type': 'Cashless hospitalisation', 'status': STATUS[row['state']],
            'procedure': case.admission.procedure, 'policy_id': case.policy.policy_id,
            'insurer': case.policy.insurer, 'plan': case.policy.product,
            'admission_date': str(case.admission.admit_date),
            'estimate': {'billed': assessment.billed, 'insurer': assessment.payable, 'patient': assessment.patient_share},
            'estimate_label': 'Estimate based on the current case bill and policy; not an insurer payment guarantee.'}


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def uploads(db_path: Path | str, claim_id: str) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute('SELECT upload_id,request_id,document_type,filename,status,reason,reviewed_by,reviewed_at,created_at FROM portal_uploads WHERE claim_id=? ORDER BY created_at DESC', (claim_id,))]


def requests(db_path: Path | str, claim_id: str) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM portal_requests WHERE claim_id=? ORDER BY created_at DESC', (claim_id,))]


def install(app, db_path: Path | str, runner, schedule, actions) -> None:
    lock = threading.RLock()

    @app.post('/api/auth/login')
    def login(body: Login, response: Response) -> dict:
        return portal_auth.login(db_path, body.username, body.password, response)

    @app.get('/api/auth/me')
    def me() -> dict:
        return user()

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response) -> dict:
        token = request.cookies.get(portal_auth.COOKIE, '')
        with connect(db_path) as conn:
            conn.execute('DELETE FROM portal_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie(portal_auth.COOKIE)
        return {'signed_out': True}

    @app.get('/api/portal/dashboard')
    def dashboard() -> dict:
        account = user('patient')
        today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
        with connect(db_path) as conn:
            enrolled = conn.execute('SELECT profile_json,approved_by FROM portal_enrollments WHERE patient_id=?', (account['patient_id'],)).fetchall()
            rows = conn.execute('SELECT c.claim_id FROM claims c JOIN admissions a USING(admission_id) WHERE a.patient_id=? ORDER BY c.rowid DESC', (account['patient_id'],)).fetchall()
            profiles = []
            for row in enrolled:
                profile = json.loads(row['profile_json'])
                expiry = profile['policy'].get('expiry_date')
                start = profile['policy'].get('coverage_start_date') or profile['policy']['start_date']
                days = (datetime.fromisoformat(expiry).date() - today).days if expiry else None
                profile['policy'].update(days_until_expiry=days, coverage_status=('Not yet active' if start > str(today) else 'Expiry not recorded' if days is None else 'Expired' if days < 0 else 'Active'))
                profile['approved_by'] = row['approved_by']
                profiles.append(profile)
            claims = []
            for row in rows:
                updates = conn.execute('SELECT update_id,snapshot_json,approved_by,created_at FROM portal_updates WHERE claim_id=? ORDER BY update_id', (row['claim_id'],)).fetchall()
                history = [{**json.loads(u['snapshot_json']), 'approved_by': u['approved_by'], 'published_at': u['created_at'], 'update_id': u['update_id']} for u in updates]
                claims.append({'claim_id': row['claim_id'], 'latest': history[-1] if history else None, 'history': history,
                               'requests': requests(db_path, row['claim_id']), 'uploads': uploads(db_path, row['claim_id'])})
        return {'account': account, 'profiles': profiles, 'claims': claims, 'as_of': str(today), 'synthetic': True}

    @app.post('/api/portal/claims/{claim_id}/uploads', status_code=201)
    def upload(claim_id: str, body: PatientUpload) -> dict:
        account = user('patient')
        row = own_claim(db_path, claim_id)
        if row['state'] in ('CLOSED', 'SETTLED'):
            raise HTTPException(409, 'This claim no longer accepts uploads')
        try:
            content = base64.b64decode(body.content_base64, validate=True)
            if len(content) > remediation.MAX_BYTES or not content.startswith(b'%PDF-'):
                raise ValueError('Upload a PDF no larger than 5 MB')
            with fitz.open(stream=content, filetype='pdf') as pdf:
                if pdf.needs_pass or not 1 <= pdf.page_count <= remediation.MAX_PAGES:
                    raise ValueError('Use an unlocked PDF with 1–20 pages')
        except (ValueError, binascii.Error, RuntimeError) as error:
            raise HTTPException(422, f'Cannot read this PDF: {error}')
        sha = hashlib.sha256(content).hexdigest()
        with lock, connect(db_path) as conn:
            if body.request_id:
                req = conn.execute('SELECT * FROM portal_requests WHERE request_id=? AND claim_id=? AND status=\'open\'', (body.request_id, claim_id)).fetchone()
                if req is None or req['document_type'] != body.type:
                    raise HTTPException(422, 'Choose an open request matching this document type')
            existing = conn.execute('SELECT upload_id,status FROM portal_uploads WHERE claim_id=? AND sha256=? AND document_type=?', (claim_id, sha, body.type)).fetchone()
            if existing:
                return {**dict(existing), 'duplicate': True}
            upload_id = secrets.token_hex(12)
            conn.execute('INSERT INTO portal_uploads(upload_id,claim_id,patient_id,request_id,document_type,filename,content,sha256) VALUES(?,?,?,?,?,?,?,?)',
                         (upload_id, claim_id, account['patient_id'], body.request_id, body.type, Path(body.filename).name, content, sha))
        audit.log(db_path, claim_id, f"patient:{account['patient_id']}", 'patient_upload_pending', {'upload_id': upload_id, 'sha256': sha})
        return {'upload_id': upload_id, 'status': 'pending', 'duplicate': False}

    @app.get('/api/portal/claims/{claim_id}/uploads/{upload_id}/file')
    def file(claim_id: str, upload_id: str) -> Response:
        own_claim(db_path, claim_id)
        with connect(db_path) as conn:
            row = conn.execute('SELECT content FROM portal_uploads WHERE upload_id=? AND claim_id=?', (upload_id, claim_id)).fetchone()
        if row is None:
            raise HTTPException(404, 'Upload not found')
        # Download instead of embedding untrusted PDFs in the application origin.
        return Response(bytes(row['content']), media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="patient-document.pdf"', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/api/patient-desk/{claim_id}')
    def desk(claim_id: str) -> dict:
        user('officer')
        own_claim(db_path, claim_id)
        value = snapshot(db_path, claim_id)
        with connect(db_path) as conn:
            last = conn.execute('SELECT approved_by,created_at FROM portal_updates WHERE claim_id=? ORDER BY update_id DESC LIMIT 1', (claim_id,)).fetchone()
        return {'preview': value, 'snapshot_hash': fingerprint(value), 'last_published': dict(last) if last else None,
                'uploads': uploads(db_path, claim_id), 'requests': requests(db_path, claim_id), 'busy': runner.busy(claim_id)}

    @app.post('/api/patient-desk/{claim_id}/publish')
    def publish(claim_id: str, body: Publish) -> dict:
        officer = user('officer')['display_name']
        if runner.busy(claim_id):
            raise HTTPException(409, 'Wait for the current checks to finish')
        value = snapshot(db_path, claim_id)
        if body.snapshot_hash != fingerprint(value):
            raise HTTPException(409, 'The preview changed. Review the latest version before publishing.')
        with lock, connect(db_path) as conn:
            previous = conn.execute('SELECT snapshot_json FROM portal_updates WHERE claim_id=? ORDER BY update_id DESC LIMIT 1', (claim_id,)).fetchone()
            duplicate = bool(previous and json.loads(previous['snapshot_json']) == value)
            if not duplicate:
                conn.execute('INSERT INTO portal_updates(claim_id,snapshot_json,approved_by) VALUES(?,?,?)', (claim_id, json.dumps(value), officer))
        if not duplicate:
            audit.log(db_path, claim_id, f'human:{officer}', 'patient_update_approved', {'snapshot_hash': body.snapshot_hash})
        return {'published': True, 'duplicate': duplicate}

    @app.post('/api/patient-desk/{claim_id}/requests', status_code=201)
    def request_document(claim_id: str, body: DocumentRequest) -> dict:
        officer = user('officer')['display_name']
        row = own_claim(db_path, claim_id)
        if row['state'] in ('SETTLED', 'CLOSED'):
            raise HTTPException(409, 'Claim is closed to new document requests')
        request_id = secrets.token_hex(12)
        with connect(db_path) as conn:
            conn.execute('INSERT INTO portal_requests(request_id,claim_id,document_type,message,officer) VALUES(?,?,?,?,?)', (request_id, claim_id, body.type, body.message.strip(), officer))
        audit.log(db_path, claim_id, f'human:{officer}', 'patient_document_requested', {'request_id': request_id, 'type': body.type, 'message': body.message})
        return {'request_id': request_id}

    @app.post('/api/patient-desk/{claim_id}/uploads/{upload_id}/review')
    def review_upload(claim_id: str, upload_id: str, body: Review) -> dict:
        officer = user('officer')['display_name']
        own_claim(db_path, claim_id)
        with lock:
            if runner.busy(claim_id):
                raise HTTPException(409, 'Wait for the current checks to finish')
            with connect(db_path) as conn:
                row = conn.execute('SELECT * FROM portal_uploads WHERE claim_id=? AND upload_id=?', (claim_id, upload_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Upload not found')
            if row['status'] != 'pending':
                raise HTTPException(409, 'This upload has already been reviewed')
            if body.accept:
                if 'recheck' not in actions(db_path, claim_id):
                    raise HTTPException(409, 'Accept documents when a draft is ready for rechecking. You can review or reject this upload now.')
                case = store.load(db_path, claim_id)
                try:
                    case, doc, duplicate = remediation.attach(db_path, claim_id, case, DocumentType(row['document_type']), row['filename'], bytes(row['content']), officer)
                except remediation.UploadRejected as error:
                    raise HTTPException(422, str(error))
                if not duplicate:
                    store.save(db_path, claim_id, case, f'Patient upload accepted by {officer}')
            with connect(db_path) as conn:
                conn.execute("UPDATE portal_uploads SET status=?,reason=?,reviewed_by=?,reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE upload_id=?", ('accepted' if body.accept else 'rejected', body.reason, officer, upload_id))
                if body.accept and row['request_id']:
                    conn.execute("UPDATE portal_requests SET status='fulfilled' WHERE request_id=?", (row['request_id'],))
            audit.log(db_path, claim_id, f'human:{officer}', 'patient_upload_reviewed', {'upload_id': upload_id, 'accepted': body.accept, 'reason': body.reason})
            if body.accept:
                return schedule(claim_id, 'patient evidence recheck', lambda: orchestrator.recheck(db_path, claim_id, store.load(db_path, claim_id), 'Officer accepted patient document'))
            return {'status': 'rejected'}
