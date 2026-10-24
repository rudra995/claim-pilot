"""Refinement of phase 7: fix what the checks found, recheck, and approve the exact new version."""

from pathlib import Path

import pytest

from backend import llm
from backend.agents import orchestrator
from backend.agents.approval import Decision, DraftChanged, decide
from backend.agents.hospital.intake import Reading, ReadDocument, ReadField, with_corrections
from backend.agents.orchestrator import ApprovalRequired, approval, open_issues, pending_draft, recheck, run_admission
from backend.db import audit
from backend.exchange import messages as exchange
from backend.models import train_cost, train_denial
from backend.negotiation import play_round
from backend.remediation import UploadRejected, attach, attachments, correct
from backend.schemas import Case, ClaimState, DocumentType, MessageType, PartialApprove, Query, Response

OFFICER = "Billing Officer"
XRAY = Path("fixtures/remediation/ADM-0002-xray-report.pdf")


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("disabled in tests")))
    monkeypatch.setenv("CLAIMPILOT_UPLOADS", str(tmp_path / "uploads"))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


def approve(pending: dict | None = None) -> Decision:
    return Decision(decision="approve", officer=OFFICER, draft_hash=pending["hash"] if pending else None)


# ---- The acceptance demo -------------------------------------------------------------------------


def test_missing_xray_attach_recheck_approve_submit(db: Path) -> None:
    """Missing X-ray -> attach synthetic X-ray -> recheck -> updated evidence and findings -> approval -> submission."""
    case = load("case2.json")
    claim_id = run_admission(case, db).claim_id
    first = pending_draft(db, claim_id)
    assert "Missing xray report" in open_issues(db, claim_id)

    case, doc, duplicate = attach(db, claim_id, case, DocumentType.XRAY_REPORT, "xray.pdf", XRAY.read_bytes(), OFFICER)
    assert not duplicate and doc.doc_id == "ADM-0002-U1"
    recheck(db, claim_id, case, f"{doc.doc_id} attached")

    second = pending_draft(db, claim_id)
    assert second["version"] == first["version"] + 1 and second["hash"] != first["hash"]
    assert doc.doc_id in second["message"]["documents"]
    assert "Missing xray report" not in open_issues(db, claim_id)  # the stale finding is gone
    change = [e["output"] for e in audit.events(db, claim_id) if e["action"] == "recheck"][-1]
    assert "Missing xray report" in change["resolved"] and change["draft_before"] == first["version"]
    assert "Room over cap" in change["remaining"]  # still true: the room is still over the cap

    with pytest.raises(DraftChanged):
        decide(db, claim_id, approve(first))  # an approval of the old version is refused
    assert decide(db, claim_id, approve(second)) == ClaimState.SUBMITTED
    play_round(db, claim_id, case)
    submit, answer = exchange.thread(db, claim_id)
    assert doc.doc_id in submit.documents
    assert isinstance(answer, PartialApprove) and answer.approved_amount == 185_800  # no query this time
    statuses = [d["status"] for d in orchestrator.drafts(db, claim_id)]
    assert statuses == ["superseded", "sent"]


def test_attaching_during_a_query_answers_it_with_the_new_record(db: Path) -> None:
    case = load("case2.json")
    claim_id = run_admission(case, db).claim_id
    decide(db, claim_id, approve())
    play_round(db, claim_id, case)
    assert isinstance(exchange.latest(db, claim_id), Query)
    assert "Not yet in the claim file" in pending_draft(db, claim_id)["message"]["answer"]

    case, doc, _ = attach(db, claim_id, case, DocumentType.XRAY_REPORT, "xray.pdf", XRAY.read_bytes(), OFFICER)
    recheck(db, claim_id, case, "X-ray attached")
    response = pending_draft(db, claim_id)
    assert response["message"]["type"] == "RESPONSE" and response["message"]["documents"] == [doc.doc_id]
    decide(db, claim_id, approve(response))
    play_round(db, claim_id, case)
    assert [m.type for m in exchange.thread(db, claim_id)][-2:] == [MessageType.RESPONSE, MessageType.PARTIAL_APPROVE]


# ---- Uploads ---------------------------------------------------------------------------------------


def test_the_same_file_twice_is_added_once(db: Path) -> None:
    case = load("case2.json")
    claim_id = run_admission(case, db).claim_id
    case, first, _ = attach(db, claim_id, case, DocumentType.XRAY_REPORT, "a.pdf", XRAY.read_bytes(), OFFICER)
    again, second, duplicate = attach(db, claim_id, case, DocumentType.XRAY_REPORT, "b.pdf", XRAY.read_bytes(), OFFICER)
    assert duplicate and second.doc_id == first.doc_id and len(again.documents) == len(case.documents)
    assert [a["doc_id"] for a in attachments(db, claim_id)] == [first.doc_id]


@pytest.mark.parametrize("content, message", [
    (b"not a pdf", "Only PDF"),
    (b"%PDF-1.4 broken", "could not be opened"),
    (b"%PDF-" + b"0" * (5 * 1024 * 1024), "limit"),
])
def test_bad_uploads_are_refused_with_a_fix(db: Path, content: bytes, message: str) -> None:
    case = load("case2.json")
    claim_id = run_admission(case, db).claim_id
    with pytest.raises(UploadRejected, match=message):
        attach(db, claim_id, case, DocumentType.XRAY_REPORT, "x.pdf", content, OFFICER)


# ---- Approvals stay bound to their draft ---------------------------------------------------------


def test_an_approval_does_not_carry_over_to_a_changed_draft(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    decide(db, claim_id, approve(pending_draft(db, claim_id)))
    assert approval(db, claim_id) is not None
    message = pending_draft(db, claim_id)["message"]
    changed = {**message, "requested_amount": message["requested_amount"] + 1}
    from backend.schemas import claim_message_adapter

    orchestrator.draft(db, claim_id, claim_message_adapter.validate_python(changed), ClaimState.PREAUTH_DRAFT)
    assert approval(db, claim_id) is None  # the approval was for the earlier content
    with pytest.raises(ApprovalRequired):
        orchestrator.send(db, claim_id, case)
    assert exchange.thread(db, claim_id) == []


def test_recheck_needs_a_draft_waiting_for_a_person(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    decide(db, claim_id, approve())
    with pytest.raises(orchestrator.InvalidTransition):
        recheck(db, claim_id, case, "too late: it was approved")


# ---- Corrections and untrusted documents ---------------------------------------------------------


def test_a_recorded_correction_is_applied_on_top_of_the_reading(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    fix = correct(db, claim_id, case, "ADM-0001-D1", "provisional_diagnosis", "Acute appendicitis", OFFICER, "Doctor confirmed on the ward")
    reading = Reading(documents=[ReadDocument(doc_id="ADM-0001-D1", type=DocumentType.ADMISSION_NOTE, fields={
        "provisional_diagnosis": ReadField(value="Appendicitis?", page=1, confidence=0.6, basis="narrative"),
    }, not_found=[])])
    field = with_corrections(reading, [fix]).documents[0].fields["provisional_diagnosis"]
    assert field.value == "Acute appendicitis" and field.basis == "corrected" and "Billing Officer" in field.text
    assert reading.documents[0].fields["provisional_diagnosis"].value == "Appendicitis?"  # the original is kept
    with pytest.raises(UploadRejected):
        correct(db, claim_id, case, "ADM-9999-D1", "x", "y", OFFICER, "z")


def test_an_instruction_inside_a_document_changes_nothing(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    injected = Path("fixtures/challenge/docs/CH-08.pdf").read_bytes()
    case, doc, _ = attach(db, claim_id, case, DocumentType.LAB_REPORT, "lab.pdf", injected, OFFICER)
    result = recheck(db, claim_id, case, "lab report attached")
    assert result.state == ClaimState.AWAITING_HUMAN  # still waiting for a person
    assert exchange.thread(db, claim_id) == []  # nothing was sent
    reader = result.results["document_reader"].output
    assert reader.next == "human_review"
    assert any(f.issue == "Instruction found in a document" for f in reader.findings)
    assert approval(db, claim_id) is None
