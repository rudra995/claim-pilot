"""The ClaimPilot API (design sections 11 and 12): REST for the screens, a WebSocket for the live trace.

    python -m uvicorn backend.api.main:app        (CLAIMPILOT_DB picks the database, default claimpilot.db)

On Windows, uvicorn's --reload hangs on the first reload (the old worker never stops), so restart
by hand after a code change.

Anything that runs agents (starting a case, sending an approved message, a new bill line, discharge,
reconciliation) runs in the background and returns 202 at once; watch it on /ws/trace. Every agent
step, message and decision is written to the audit log as it happens, and the WebSocket streams the
log, so the trace shows exactly what the audit tab will.

The API changes a claim only through the orchestrator and the approval gate, like the CLI does.
"""

import asyncio
import base64
import binascii
import os
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from backend.agents import orchestrator
from backend import remediation
from backend.agents.approval import Decision, DraftChanged, decide
from backend.agents.orchestrator import InvalidTransition, claim_state, pending_draft
from backend.api import store
from backend.api.runner import Busy, Runner
from backend.db import audit
from backend.db.db import DEFAULT_DB_PATH, connect, init_db
from backend.exchange import messages as exchange
from backend import llm
from backend.negotiation import INTERRUPTIBLE, play_round, resume
from backend.rules.engine import billed, portfolio, total
from backend.schemas import BillLine, ClaimMessage, ClaimState, DocumentType, Query as InsurerQuery, Sender

POLL_SECONDS = 0.25  # how often the trace checks the audit log for new steps
PING_EVERY = 8  # idle polls between keep-alive pings
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite dev server


class StartRequest(BaseModel):
    case_id: str


class BillUpdate(BaseModel):
    lines: list[BillLine] = Field(min_length=1)


class PaymentAdvice(BaseModel):
    paid: int = Field(ge=0)
    reference: str = Field(min_length=1)


class Upload(BaseModel):
    type: DocumentType
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=8, max_length=7_200_000)  # about 5 MB once decoded
    officer: str = Field(min_length=1, max_length=80)


class Correction(BaseModel):
    doc_id: str
    field: str
    value: str = Field(min_length=1, max_length=500)
    officer: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=500)


class RecheckRequest(BaseModel):
    reason: str = Field(default="Evidence changed", min_length=1, max_length=300)


# Folders a document file may be served from; anything else is refused.
SERVABLE = [Path("fixtures"), Path("data/documents")]


def officer_tokens() -> dict[str, str]:
    """CLAIMPILOT_OFFICER_TOKENS="Asha Rao=token1,Vikram Shah=token2": token -> name. Empty: demo mode."""
    pairs = [item.split("=", 1) for item in os.environ.get("CLAIMPILOT_OFFICER_TOKENS", "").split(",") if "=" in item]
    return {token.strip(): name.strip() for name, token in pairs if token.strip()}


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    init_db(db_path).close()
    runner = Runner(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        runner.shutdown()

    app = FastAPI(title="ClaimPilot", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.runner = runner
    app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    def existing(claim_id: str) -> ClaimState:
        try:
            return claim_state(db_path, claim_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No claim {claim_id}")

    def schedule(claim_id: str, label: str, work) -> dict[str, Any]:
        try:
            runner.submit(claim_id, label, work)
        except Busy as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error))
        return {"claim_id": claim_id, "state": claim_state(db_path, claim_id), "job": label}

    def officer(claimed: str, token: str | None) -> tuple[str, str]:
        """(name, identity). With tokens configured the token decides who is acting; otherwise the name is self-declared."""
        tokens = officer_tokens()
        if not tokens:
            return claimed, "self-declared"
        if token not in tokens:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "An officer token is required (X-Officer-Token)")
        return tokens[token], "token"

    def not_busy(claim_id: str) -> None:
        if runner.busy(claim_id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{claim_id} is still working on its last step")

    def move_on(claim_id: str) -> None:
        """A stage the insurer approved moves on: back to treatment, or to final approval after discharge."""
        if claim_state(db_path, claim_id) in (ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED):
            orchestrator.next_stage(db_path, claim_id)

    def play(claim_id: str) -> None:
        play_round(db_path, claim_id, store.load(db_path, claim_id))
        move_on(claim_id)

    # ---- Cases and claims ------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """The API is up. For whether the models and LLM are ready, see /api/ready."""
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready() -> dict[str, Any]:
        """What works right now: database, trained models, policy search, the LLM (live or template fallback)."""
        from backend.models import train_cost, train_denial

        models = {
            "cost": all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES),
            "denial": train_denial.model_path().exists(),
        }
        try:
            uploads = remediation.uploads_dir()
            uploads.mkdir(parents=True, exist_ok=True)
            writable = os.access(uploads, os.W_OK)
        except OSError:
            writable = False
        rows = _claim_rows(db_path)
        interrupted = [r["claim_id"] for r in rows if r["state"] in INTERRUPTIBLE | {ClaimState.SUBMITTED} and not runner.busy(r["claim_id"])]
        problems = [f"{name} model not trained: run python -m backend.models.train_{name}" for name, ok in models.items() if not ok]
        return {
            "api": "ok",
            "database": str(db_path),
            "models": models,
            "policy_search": Path("data/chroma").exists(),
            "llm": llm.status(),
            "uploads_writable": writable,
            "interrupted_claims": interrupted,
            "ready": all(models.values()) and writable,
            "problems": problems,
        }

    @app.get("/api/cases")
    def cases() -> list[dict[str, Any]]:
        """Patients ready to admit: the scripted demo cases and the preset patients."""
        return [
            {
                "case_id": c.case_id,
                "title": c.title,
                "demo": store.is_demo(c),
                "patient": c.patient.name,
                "age": c.patient.age,
                "gender": c.patient.gender,
                "city": c.patient.city,
                "procedure": c.admission.procedure,
                "emergency": c.admission.emergency,
                "room_category": c.admission.room_category,
                "stay_days": (c.admission.discharge_date - c.admission.admit_date).days,
                "tpa_id": c.admission.tpa_id,
                "insurer": c.policy.insurer,
                "product": c.policy.product,
                "sum_insured": c.policy.sum_insured,
                "documents": len(c.documents),
                "estimated_bill": billed(c.bill_lines),
            }
            for c in store.fixture_cases().values()
        ]

    @app.post("/api/cases/start", status_code=status.HTTP_202_ACCEPTED)
    def start(request: StartRequest) -> dict[str, Any]:
        case = store.fixture_cases().get(request.case_id)
        if case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No case {request.case_id}")
        claim_id = orchestrator.open_claim(db_path, case)
        store.save(db_path, claim_id, case, "Claim opened")
        return schedule(claim_id, "admission", lambda: orchestrator.admit(db_path, claim_id, case))

    @app.get("/api/claims")
    def claims() -> list[dict[str, Any]]:
        return [_summary(db_path, row, runner) for row in _claim_rows(db_path)]

    @app.get("/api/claims/{claim_id}")
    def claim(claim_id: str) -> dict[str, Any]:
        existing(claim_id)
        events = audit.events(db_path, claim_id)
        case = store.from_events(events)
        if case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No case file for {claim_id}")
        return {
            **_summary(db_path, _claim_rows(db_path, claim_id)[0], runner, events),
            "case": case.model_dump(mode="json"),
            "pending_draft": pending_draft(db_path, claim_id),
            "actions": _actions(db_path, claim_id),
            "agents": _latest_outputs(events),
            "thread": [_message(m) for m in exchange.thread(db_path, claim_id)],
            "timeline": [_event({**e, "claim_id": claim_id}) for e in events],
            "drafts": orchestrator.drafts(db_path, claim_id),
            "attachments": remediation.attachments(db_path, claim_id),
            "corrections": remediation.corrections(db_path, claim_id),
            "tasks": remediation.tasks(_latest_outputs(events)),
            "last_recheck": next((e["output"] | {"at": e["created_at"]} for e in reversed(events) if e["action"] == "recheck"), None),
            "evidence": _evidence(_latest_outputs(events)),
            "totals": _totals(case, exchange.thread(db_path, claim_id)),
        }

    @app.get("/api/claims/{claim_id}/thread")
    def thread(claim_id: str) -> list[dict[str, Any]]:
        existing(claim_id)
        return [_message(m) for m in exchange.thread(db_path, claim_id)]

    @app.get("/api/claims/{claim_id}/audit")
    def audit_trail(claim_id: str, after: int = 0) -> list[dict[str, Any]]:
        existing(claim_id)
        return [_event(e) for e in audit.since(db_path, after, claim_id)]

    @app.get("/api/queue")
    def queue() -> list[dict[str, Any]]:
        """Claims waiting for a person, with what they'd send and why they were flagged."""
        waiting = [row for row in _claim_rows(db_path) if row["state"] == ClaimState.AWAITING_HUMAN]
        out = []
        for row in waiting:
            events = audit.events(db_path, row["claim_id"])
            out.append({
                **_summary(db_path, row, runner, events),
                "pending_draft": pending_draft(db_path, row["claim_id"]),
                "actions": _actions(db_path, row["claim_id"]),
                "agents": _latest_outputs(events),
            })
        return out

    # ---- Human gate --------------------------------------------------------------------------

    @app.post("/api/claims/{claim_id}/decision")
    def decision(claim_id: str, body: Decision, x_officer_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Approve, edit, reject or accept. An approved message is then sent and the insurer answers.

        Send the `draft_hash` you reviewed: if the draft has changed since, the decision is refused.
        A repeat of a decision already recorded (a double click, a retry) changes nothing.
        """
        existing(claim_id)
        name, identity = officer(body.officer, x_officer_token)
        body = body.model_copy(update={"officer": name, "identity": identity})
        if repeat := _repeat(db_path, claim_id, body):
            return {"claim_id": claim_id, "state": claim_state(db_path, claim_id), "job": None, "duplicate": True, "decided": repeat}
        not_busy(claim_id)
        try:
            state = decide(db_path, claim_id, body)
        except DraftChanged as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error))
        except ValueError as error:  # not waiting, nothing to send, nothing to accept
            raise HTTPException(status.HTTP_409_CONFLICT, str(error))
        if state == ClaimState.SUBMITTED:
            return schedule(claim_id, "negotiation", lambda: play(claim_id))
        if body.decision == "accept":
            move_on(claim_id)
        return {"claim_id": claim_id, "state": claim_state(db_path, claim_id), "job": None}

    @app.post("/api/claims/{claim_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    def resume_claim(claim_id: str) -> dict[str, Any]:
        """Carry on a claim whose run stopped part-way, from the last step on record."""
        state = existing(claim_id)
        if state not in INTERRUPTIBLE | {ClaimState.SUBMITTED}:
            raise HTTPException(status.HTTP_409_CONFLICT, f"The claim is {state}; nothing to resume")

        def work() -> None:
            resume(db_path, claim_id, store.load(db_path, claim_id))
            move_on(claim_id)

        return schedule(claim_id, "resume", work)

    # ---- Fix and recheck -------------------------------------------------------------------

    @app.post("/api/claims/{claim_id}/documents", status_code=status.HTTP_201_CREATED)
    def attach_document(claim_id: str, body: Upload, x_officer_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Attach a synthetic PDF (base64). Then call /recheck to rerun the checks on it."""
        state = existing(claim_id)
        if state in (ClaimState.SETTLED, ClaimState.CLOSED):
            raise HTTPException(status.HTTP_409_CONFLICT, f"The claim is {state}; documents can't be added now")
        not_busy(claim_id)
        name, _ = officer(body.officer, x_officer_token)
        try:
            content = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "content_base64 is not valid base64")
        case = store.load(db_path, claim_id)
        try:
            case, doc, duplicate = remediation.attach(db_path, claim_id, case, body.type, body.filename, content, name)
        except remediation.UploadRejected as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))
        if not duplicate:
            store.save(db_path, claim_id, case, f"{doc.doc_id} attached by {name}")
        return {"document": doc.model_dump(mode="json", exclude={"fields"}), "duplicate": duplicate, "recheck": "recheck" in _actions(db_path, claim_id)}

    @app.post("/api/claims/{claim_id}/corrections", status_code=status.HTTP_201_CREATED)
    def record_correction(claim_id: str, body: Correction, x_officer_token: str | None = Header(default=None)) -> dict[str, Any]:
        existing(claim_id)
        name, _ = officer(body.officer, x_officer_token)
        try:
            return remediation.correct(db_path, claim_id, store.load(db_path, claim_id), body.doc_id, body.field, body.value, name, body.reason)
        except remediation.UploadRejected as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))

    @app.post("/api/claims/{claim_id}/recheck", status_code=status.HTTP_202_ACCEPTED)
    def recheck(claim_id: str, body: RecheckRequest | None = None) -> dict[str, Any]:
        """Re-read the documents, rerun the checks that depend on them, and draft a new version for approval."""
        existing(claim_id)
        if "recheck" not in _actions(db_path, claim_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to recheck: no draft is waiting for a person")
        reason = (body or RecheckRequest()).reason
        return schedule(claim_id, "recheck", lambda: orchestrator.recheck(db_path, claim_id, store.load(db_path, claim_id), reason))

    @app.get("/api/claims/{claim_id}/documents/{doc_id}/file")
    def document_file(claim_id: str, doc_id: str) -> FileResponse:
        """A claim document's PDF, for following a source to its page (add #page=N in the browser)."""
        existing(claim_id)
        doc = next((d for d in store.load(db_path, claim_id).documents if d.doc_id == doc_id), None)
        if doc is None or doc.file_path is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No document {doc_id} in {claim_id}")
        path = Path(doc.file_path).resolve()
        roots = [root.resolve() for root in [*SERVABLE, remediation.uploads_dir()]]
        if not path.exists() or not any(path.is_relative_to(root) for root in roots) or path.suffix.lower() != ".pdf":
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{doc_id}'s file is not available")
        return FileResponse(path, media_type="application/pdf", filename=f"{doc_id}.pdf", content_disposition_type="inline")

    # ---- Rest of the stay ------------------------------------------------------------------

    @app.post("/api/claims/{claim_id}/bill", status_code=status.HTTP_202_ACCEPTED)
    def add_bill_lines(claim_id: str, body: BillUpdate) -> dict[str, Any]:
        """New charges on the running bill; the enhancement agent checks them against the approval."""
        _require(existing(claim_id), ClaimState.IN_TREATMENT)
        not_busy(claim_id)
        case = store.load(db_path, claim_id)
        try:
            case = case.model_validate({**case.model_dump(), "bill_lines": [*case.bill_lines, *body.lines]})
        except ValidationError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, error.errors(include_url=False, include_context=False))
        store.save(db_path, claim_id, case, f"{len(body.lines)} bill line(s) added")
        return schedule(claim_id, "enhancement", lambda: orchestrator.watch_bill(db_path, claim_id, case))

    @app.post("/api/claims/{claim_id}/redraft", status_code=status.HTTP_202_ACCEPTED)
    def redraft(claim_id: str) -> dict[str, Any]:
        """A pre-auth sent back by a person: the agents prepare it again."""
        _require(existing(claim_id), ClaimState.PREAUTH_DRAFT)
        return schedule(claim_id, "redraft", lambda: orchestrator.redraft(db_path, claim_id, store.load(db_path, claim_id)))

    @app.post("/api/claims/{claim_id}/discharge", status_code=status.HTTP_202_ACCEPTED)
    def start_discharge(claim_id: str) -> dict[str, Any]:
        _require(existing(claim_id), ClaimState.IN_TREATMENT)
        return schedule(claim_id, "discharge", lambda: orchestrator.discharge(db_path, claim_id, store.load(db_path, claim_id)))

    @app.post("/api/claims/{claim_id}/payment", status_code=status.HTTP_202_ACCEPTED)
    def record_payment(claim_id: str, body: PaymentAdvice) -> dict[str, Any]:
        """The insurer's payment advice: the claim is settled, then reconciled."""
        _require(existing(claim_id), ClaimState.FINAL_APPROVED)
        not_busy(claim_id)
        orchestrator.settle(db_path, claim_id, body.paid, body.reference)
        return schedule(claim_id, "reconciliation", lambda: orchestrator.reconcile(db_path, claim_id, store.load(db_path, claim_id)))

    @app.post("/api/claims/{claim_id}/patient-note")
    def patient_note(claim_id: str) -> dict[str, Any]:
        """The patient's cost note and room advice, for staff to check before handing it over."""
        existing(claim_id)
        result = orchestrator.advise_patient(db_path, claim_id, store.load(db_path, claim_id))
        if result is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The patient agent failed twice")
        return {"output": result.output.model_dump(mode="json"), "note": result.data.model_dump(mode="json")}

    # ---- Dashboard -------------------------------------------------------------------------

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        return _dashboard(db_path)

    # ---- Live trace ------------------------------------------------------------------------

    @app.websocket("/ws/trace")
    async def trace(websocket: WebSocket, claim_id: str | None = None, after: int = Query(0)) -> None:
        """Streams audit events as they're written. `after=-1` starts from now; a claim_id narrows it to one claim."""
        await websocket.accept()
        last = _last_event_id(db_path) if after < 0 else after
        idle = 0
        try:
            while True:
                events = await asyncio.to_thread(audit.since, db_path, last, claim_id)
                for event in events:
                    await websocket.send_json({"type": "event", **_event(event)})
                    last = event["event_id"]
                idle = 0 if events else idle + 1
                if idle >= PING_EVERY:
                    await websocket.send_json({"type": "ping", "last_event_id": last})
                    idle = 0
                await asyncio.sleep(POLL_SECONDS)
        except WebSocketDisconnect:
            return

    return app


# ---- Helpers --------------------------------------------------------------------------------


def _require(state: ClaimState, wanted: ClaimState) -> None:
    if state != wanted:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The claim is {state}, not {wanted}")


def _claim_rows(db_path: Path | str, claim_id: str | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT c.claim_id, c.admission_id, c.state, c.requested, c.approved, a.procedure, a.tpa_id, a.room_category,"
        " p.name AS patient, p.age, pol.insurer, pol.sum_insured"
        " FROM claims c JOIN admissions a USING (admission_id) JOIN patients p USING (patient_id)"
        " JOIN policies pol ON pol.policy_id = a.policy_id"
    )
    params: tuple = ()
    if claim_id is not None:
        query += " WHERE c.claim_id = ?"
        params = (claim_id,)
    conn = connect(db_path)
    try:
        return [dict(row) for row in conn.execute(query + " ORDER BY c.rowid", params).fetchall()]
    finally:
        conn.close()


def _summary(db_path: Path | str, row: dict[str, Any], runner: Runner, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    events = audit.events(db_path, row["claim_id"]) if events is None else events
    case = store.from_events(events)
    risk = _latest_outputs(events).get("denial_risk")
    transitions = [e for e in events if e["action"] == "transition"]
    return {
        **row,
        "title": case.title if case else None,
        "case_id": case.case_id if case else None,
        "risk": risk["data"]["risk"] if risk else None,
        "flags": _flags(events),
        "last_reason": transitions[-1]["output"]["reason"] if transitions else None,
        "updated_at": events[-1]["created_at"] if events else None,
        "busy": runner.busy(row["claim_id"]),
        "errors": [e["output"] for e in events if e["action"] == "run_error"],
    }


def _latest_outputs(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each agent's most recent output and data."""
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["action"] == "agent_output":
            latest[event["actor"]] = {**event["output"], "event_id": event["event_id"], "created_at": event["created_at"]}
    return latest


def _flags(events: list[dict[str, Any]]) -> list[str]:
    """What asked for a person since the claim last joined the queue."""
    since = max((i for i, e in enumerate(events) if e["action"] == "transition" and e["output"]["to"] == ClaimState.AWAITING_HUMAN), default=None)
    if since is None:
        return []
    # the work since the last decision or message is what led to this gate
    start = max((i for i, e in enumerate(events[:since]) if e["action"] in ("human_decision", "message_sent")), default=0)
    flags = [f"{e['actor']}: {e['output']['output']['decision']}" for e in events[start:since] if e["action"] == "agent_output" and e["output"]["output"]["next"] == "human_review"]
    flags += [f"Escalated: {e['output']['agent']}" for e in events[start:since] if e["action"] == "escalate"]
    return [*flags, events[since]["output"]["reason"]]


def _actions(db_path: Path | str, claim_id: str) -> list[str]:
    state = claim_state(db_path, claim_id)
    actions: list[str] = []
    pending = pending_draft(db_path, claim_id)
    if state == ClaimState.AWAITING_HUMAN:
        if pending is not None:
            actions += ["approve", "edit"]
        actions.append("reject")
        last = exchange.latest(db_path, claim_id)
        if last is not None and last.sender == Sender.INSURER and not isinstance(last, InsurerQuery):
            actions.append("accept")
        if pending is not None and ClaimState(pending["from_state"]) in orchestrator.RECHECKABLE:
            actions.append("recheck")
    elif state == ClaimState.IN_TREATMENT:
        actions += ["bill", "discharge"]
    elif state == ClaimState.FINAL_APPROVED:
        actions.append("payment")
    elif state == ClaimState.PREAUTH_DRAFT:
        actions += ["redraft", "recheck"]
    if state not in (ClaimState.SETTLED, ClaimState.CLOSED):
        actions.append("attach")
    if state != ClaimState.CLOSED:
        actions.append("patient_note")
    return actions


def _repeat(db_path: Path | str, claim_id: str, body: Decision) -> dict[str, Any] | None:
    """The recorded decision this request repeats, if it is a retry of the last one on the same draft."""
    if claim_state(db_path, claim_id) == ClaimState.AWAITING_HUMAN or not body.draft_hash:
        return None
    last = next((e["output"] for e in reversed(audit.events(db_path, claim_id)) if e["action"] == "human_decision"), None)
    if last and last.get("draft_hash") == body.draft_hash and last["decision"] == body.decision:
        return last
    return None


def _evidence(latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Structured evidence behind the latest checks: each passage with its document, page and what it shows."""
    out = []
    for actor, key in (("appeal", "evidence"), ("tpa_medical_reviewer", "necessity")):
        assessment = (latest.get(actor) or {}).get("data", {}).get(key)
        if assessment:
            out.append({
                "by": actor, "assertion": assessment["assertion"], "verdict": assessment["verdict"], "reason": assessment["reason"],
                "passages": assessment["passages"], "problems": assessment["problems"],
            })
    return out


def _totals(case, thread: list[ClaimMessage]) -> dict[str, Any]:
    """Rupee totals the screens show, worked out by the rules engine's helpers rather than in the browser."""
    return {
        "billed": billed(case.bill_lines) if case else None,
        "deducted_by_message": [total(d.amount for d in getattr(m, "deductions", [])) for m in thread],
    }


def _message(message: ClaimMessage) -> dict[str, Any]:
    body = message.model_dump(mode="json")
    if "package" in body:
        body["package"] = {key: value for key, value in body["package"].items() if key != "case"}  # the claim file is on the claim
    return {**body, "sender": message.sender.value}


def _event(event: dict[str, Any]) -> dict[str, Any]:
    output = event["output"]
    if event["action"] == "case_file":
        output = {"reason": output["reason"]}
    return {
        "event_id": event["event_id"],
        "claim_id": event.get("claim_id"),
        "actor": event["actor"],
        "action": event["action"],
        "created_at": event["created_at"],
        "summary": _describe(event["actor"], event["action"], event["output"]),
        "output": output,
    }


def _describe(actor: str, action: str, output: Any) -> str:
    """One line for the trace."""
    if action == "agent_output":
        result = output["output"]
        return f"{actor}: {result['decision']} (confidence {result['confidence']:.2f}) -> {result['next']}"
    if action == "transition":
        return f"{output['from']} -> {output['to']}: {output['reason']}"
    if action == "message_sent":
        return f"{actor} sent {output['type']} ({output['stage']}, round {output['round']})"
    if action == "human_decision":
        return f"{actor.removeprefix('human:')} chose {output['decision']}" + (f": {output['reason']}" if output.get("reason") else "")
    if action == "llm_call":
        return f"{actor} asked the LLM (prompt masked)"
    if action == "draft_message":
        return f"Drafted {output['message']['type']} for approval"
    if action == "agent_error":
        return f"{actor} failed (attempt {output['attempt']}): {output['error']}"
    if action == "escalate":
        return f"Escalated to a person: {output['reason']}"
    if action == "run_error":
        return f"{output['job']} stopped: {output['error']}"
    if action == "case_file":
        return f"Case file saved: {output['reason']}"
    return f"{actor}: {action}"


def _last_event_id(db_path: Path | str) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


def _dashboard(db_path: Path | str) -> dict[str, Any]:
    """Headline numbers across all claims. Phase 12 adds the learning loop and fairness panels."""
    rows = _claim_rows(db_path)
    first_pass, first_answers, rounds, clauses, queries = 0, 0, [], Counter(), Counter()
    clause_amounts: Counter = Counter()
    for row in rows:
        messages = exchange.thread(db_path, row["claim_id"])
        stages = {m.stage for m in messages}
        for stage in stages:
            answers = [m for m in messages if m.stage == stage and m.sender == Sender.INSURER]
            if not answers:
                continue
            if stage == "preauth":
                first_answers += 1
                first_pass += not isinstance(answers[0], InsurerQuery) and answers[0].type in ("APPROVE", "PARTIAL_APPROVE")
            decided = [m for m in answers if not isinstance(m, InsurerQuery)]
            if decided:
                rounds.append(decided[-1].round)
                for d in getattr(decided[-1], "deductions", []):
                    clauses[d.clause] += 1
                    clause_amounts[d.clause] += d.amount
            queries.update(m.missing for m in answers if isinstance(m, InsurerQuery))
    states = Counter(row["state"] for row in rows)
    return {
        "claims": len(rows),
        "by_state": dict(states),
        "awaiting_human": states.get(ClaimState.AWAITING_HUMAN, 0),
        "first_pass_rate": round(first_pass / first_answers, 3) if first_answers else None,
        "average_rounds": round(sum(rounds) / len(rounds), 2) if rounds else None,
        **portfolio(rows),
        "top_deductions": [{"clause": c, "count": n, "amount": clause_amounts[c]} for c, n in clauses.most_common(5)],
        "top_queries": [{"missing": q, "count": n} for q, n in queries.most_common(5)],
    }


app = create_app(os.environ.get("CLAIMPILOT_DB", DEFAULT_DB_PATH))
