"""Cookie sessions and role boundaries for the synthetic patient/officer demo."""
import hashlib
import hmac
import json
import os
import secrets
import time
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response
from starlette.responses import JSONResponse

from backend.db.db import connect

COOKIE = "claimpilot_session"
DEV_ORIGINS = {'http://127.0.0.1:5173', 'http://localhost:5173'}
current_user: ContextVar[dict | None] = ContextVar("portal_user", default=None)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}:{value}"


def seed(db_path: Path | str) -> None:
    """Explicit synthetic fixture enrollments include a recorded demo officer approval."""
    from backend.api.store import fixture_cases
    fixture = json.loads(Path('fixtures/portal_accounts.json').read_text(encoding='utf-8'))
    cases = fixture_cases()
    with connect(db_path) as conn:
        for item in fixture:
            case = cases.get(item.get('case_id'))
            if item['role'] == 'patient' and case is None:
                continue
            patient_id = case.patient.patient_id if case else None
            if not conn.execute('SELECT 1 FROM portal_accounts WHERE username=?', (item['username'],)).fetchone():
                conn.execute('INSERT INTO portal_accounts VALUES (?,?,?,?,?,?)', (
                    item['username'], item['username'], password_hash(item['password']), item['role'],
                    case.patient.name if case else item['name'], patient_id))
            if case:
                profile = {'patient': case.patient.model_dump(mode='json'), 'policy': case.policy.model_dump(mode='json', exclude={'rules'}), 'claim_type': 'Cashless hospitalisation'}
                conn.execute('INSERT OR IGNORE INTO portal_enrollments VALUES (?,?,?,?)',
                             (patient_id, case.policy.policy_id, json.dumps(profile), 'Demo Billing Officer — synthetic enrollment approval'))


def session_user(db_path: Path | str, token: str | None) -> dict | None:
    if not token:
        return None
    with connect(db_path) as conn:
        row = conn.execute('SELECT a.account_id,a.role,a.display_name,a.patient_id FROM portal_sessions s JOIN portal_accounts a USING(account_id) WHERE token_hash=? AND expires_at>?',
                           (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    return dict(row) if row else None


def login(db_path: Path | str, username: str, password: str, response: Response) -> dict:
    with connect(db_path) as conn:
        row = conn.execute('SELECT * FROM portal_accounts WHERE username=?', (username.strip().lower(),)).fetchone()
        stored = row['password_hash'] if row else '00' * 16 + ':' + '00' * 32
        candidate = password_hash(password, stored.split(':')[0])
        if not hmac.compare_digest(candidate, stored) or row is None:
            raise HTTPException(401, 'Incorrect username or password')
        token = secrets.token_urlsafe(32)
        conn.execute('DELETE FROM portal_sessions WHERE expires_at<=?', (int(time.time()),))
        conn.execute('INSERT INTO portal_sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['account_id'], int(time.time()) + 28800))
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', secure=os.environ.get('CLAIMPILOT_SECURE_COOKIE') == '1', max_age=28800)
    return session_user(db_path, token)


def install(app, db_path: Path | str) -> None:
    # Single-process demo rate limit. Deployment should use a shared gateway limit.
    attempts: dict[str, list[float]] = {}

    @app.middleware('http')
    async def authorize(request: Request, call_next):
        path = request.url.path
        if not path.startswith('/api/'):
            return await call_next(request)
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            if origin not in DEV_ORIGINS and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'Cross-origin mutation refused'}, status_code=403)
        if path == '/api/auth/login':
            key = request.client.host if request.client else 'unknown'
            now = time.time()
            attempts[key] = [t for t in attempts.get(key, []) if t > now - 60]
            if len(attempts[key]) >= 20:
                return JSONResponse({'detail': 'Too many sign-in attempts. Try again in a minute.'}, status_code=429)
            attempts[key].append(now)
        user = session_user(db_path, request.cookies.get(COOKIE))
        public = path in ('/api/auth/login', '/api/auth/logout', '/api/auth/me', '/api/health')
        if not public and user is None:
            return JSONResponse({'detail': 'Please sign in'}, status_code=401)
        if not public and not path.startswith('/api/portal/') and user['role'] != 'officer':
            return JSONResponse({'detail': 'Billing officer access required'}, status_code=403)
        token = current_user.set(user)
        try:
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        finally:
            current_user.reset(token)
