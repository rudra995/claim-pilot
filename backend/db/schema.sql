-- ClaimPilot database (design section 8). SQLite. Amounts are whole rupees, dates are ISO strings.

CREATE TABLE IF NOT EXISTS patients (
    patient_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    age         INTEGER NOT NULL,
    gender      TEXT NOT NULL,
    city        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    policy_id   TEXT PRIMARY KEY,
    insurer     TEXT NOT NULL,
    product     TEXT NOT NULL,
    sum_insured INTEGER NOT NULL,
    start_date  TEXT NOT NULL
);

-- value_json holds the whole Rule (backend/schemas/rules.py) as JSON.
CREATE TABLE IF NOT EXISTS rules (
    rule_id     TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL REFERENCES policies(policy_id),
    type        TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    clause_id   TEXT NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admissions (
    admission_id   TEXT PRIMARY KEY,
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    policy_id      TEXT NOT NULL REFERENCES policies(policy_id),
    procedure      TEXT NOT NULL,
    icd10          TEXT NOT NULL,
    room_category  TEXT NOT NULL,
    admit_date     TEXT NOT NULL,
    discharge_date TEXT,
    tpa_id         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bill_lines (
    line_id      TEXT PRIMARY KEY,
    admission_id TEXT NOT NULL REFERENCES admissions(admission_id),
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    amount       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    admission_id   TEXT NOT NULL REFERENCES admissions(admission_id),
    type           TEXT NOT NULL,
    file_path      TEXT,
    extracted_json TEXT
);

-- state must be a ClaimState value (backend/schemas/claim_state.py).
CREATE TABLE IF NOT EXISTS claims (
    claim_id     TEXT PRIMARY KEY,
    admission_id TEXT NOT NULL REFERENCES admissions(admission_id),
    state        TEXT NOT NULL CHECK (state IN (
        'ADMITTED', 'PREAUTH_DRAFT', 'RISK_CHECK', 'AWAITING_HUMAN', 'SUBMITTED',
        'QUERY_RAISED', 'APPROVED', 'PARTIALLY_APPROVED', 'DENIED', 'IN_TREATMENT',
        'DISCHARGE_DRAFT', 'FINAL_APPROVED', 'SETTLED', 'CLOSED'
    )),
    requested    INTEGER,
    approved     INTEGER,
    risk_score   REAL
);

-- The claim exchange: append-only.
CREATE TABLE IF NOT EXISTS claim_messages (
    msg_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id   TEXT NOT NULL REFERENCES claims(claim_id),
    type       TEXT NOT NULL,
    sender     TEXT NOT NULL CHECK (sender IN ('hospital', 'insurer')),
    round      INTEGER NOT NULL,
    body_json  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Every agent and human action: append-only.
CREATE TABLE IF NOT EXISTS audit_log (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id    TEXT REFERENCES claims(claim_id),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    input_hash  TEXT,
    output_json TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TRIGGER IF NOT EXISTS claim_messages_no_update BEFORE UPDATE ON claim_messages
BEGIN SELECT RAISE(ABORT, 'claim_messages is append-only'); END;
CREATE TRIGGER IF NOT EXISTS claim_messages_no_delete BEFORE DELETE ON claim_messages
BEGIN SELECT RAISE(ABORT, 'claim_messages is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Patient portal. Existing claims and state transitions remain owned by the orchestrator.
CREATE TABLE IF NOT EXISTS portal_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('patient', 'officer')),
    display_name TEXT NOT NULL,
    patient_id TEXT,
    CHECK ((role = 'patient' AND patient_id IS NOT NULL) OR (role = 'officer' AND patient_id IS NULL))
);
CREATE TABLE IF NOT EXISTS portal_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES portal_accounts(account_id),
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS portal_enrollments (
    patient_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    PRIMARY KEY(patient_id, policy_id)
);
CREATE TABLE IF NOT EXISTS portal_updates (
    update_id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    snapshot_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS portal_requests (
    request_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    document_type TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'fulfilled')),
    officer TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS portal_uploads (
    upload_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    patient_id TEXT NOT NULL,
    request_id TEXT REFERENCES portal_requests(request_id),
    document_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    content BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'rejected')),
    reason TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(claim_id, sha256, document_type)
);
