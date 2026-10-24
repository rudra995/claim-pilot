"""Phase 7: denial-risk agent, the human approval gate, and masking before every LLM call."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend import llm
from backend.agents.approval import Decision, decide
from backend.agents.hospital import denial_risk
from backend.agents.orchestrator import ApprovalRequired, InvalidTransition, run_admission, transition
from backend.db import audit
from backend.db.db import connect
from backend.models import train_cost, train_denial
from backend.privacy import Masker
from backend.schemas import Case, ClaimState
from data.generator.render import render_pdf


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stands in for the model: records what it was sent and answers with the first token it saw."""
    sent: list[str] = []

    def answer(prompt: str, system: str, seed: int | None, temperature: float) -> str:
        sent.append(prompt)
        token = next((word for word in prompt.split() if word.startswith("[NAME_")), "")
        return f"Justification for {token} who needs admission."

    monkeypatch.setattr(llm, "_answer", answer)
    return sent


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


def approve(officer: str = "Billing Officer") -> Decision:
    return Decision(decision="approve", officer=officer)


# ---- Denial-risk agent ---------------------------------------------------------------------


def test_case2_flags_missing_xray_and_room_cap_with_sources() -> None:
    result = denial_risk.run("CLM-TEST", load("case2.json"))
    items = {item.flag: item for item in result.data.items}
    assert items["missing_xray_report"].source == "Document checklist for total knee replacement"
    assert items["room_over_cap"].source == "Policy clause 4.2"
    assert "₹47,200" in items["room_over_cap"].evidence  # room ₹8,000 + linked charges ₹39,200, from the engine
    assert items["missing_xray_report"].fix == "Attach the pre-op X-ray report"
    assert result.output.decision == "fix_before_submit"
    assert result.output.next == "human_review"  # risk above the threshold
    assert all(f.source for f in result.output.findings)


def test_clean_case_has_nothing_to_fix() -> None:
    result = denial_risk.run("CLM-TEST", load("case1.json"))
    assert result.data.items == []
    assert result.output.decision == "ok_to_submit"
    assert result.data.risk < denial_risk.RISK_THRESHOLD


def test_risk_check_runs_in_the_admission_flow(db: Path, prompts: list[str]) -> None:
    result = run_admission(load("case2.json"), db)
    assert result.state == ClaimState.AWAITING_HUMAN
    assert "denial_risk" in result.results
    assert "denial_risk: fix_before_submit" in result.review_flags


# ---- Approval gate ---------------------------------------------------------------------------


def test_cannot_submit_without_approval(db: Path, prompts: list[str]) -> None:
    claim_id = run_admission(load("case1.json"), db).claim_id
    with pytest.raises(ApprovalRequired):
        transition(db, claim_id, ClaimState.SUBMITTED, "trying to skip the gate")
    assert decide(db, claim_id, approve()) == ClaimState.SUBMITTED


def test_approval_from_an_earlier_round_does_not_count(db: Path, prompts: list[str]) -> None:
    claim_id = run_admission(load("case1.json"), db).claim_id
    audit.log(db, claim_id, "human:Billing Officer", "human_decision", approve().model_dump(mode="json"))
    transition(db, claim_id, ClaimState.PREAUTH_DRAFT, "reworked")
    transition(db, claim_id, ClaimState.AWAITING_HUMAN, "back in the queue")
    with pytest.raises(ApprovalRequired):
        transition(db, claim_id, ClaimState.SUBMITTED, "old approval")


def test_reject_sends_claim_back_and_it_cannot_be_submitted(db: Path, prompts: list[str]) -> None:
    claim_id = run_admission(load("case2.json"), db).claim_id
    state = decide(db, claim_id, Decision(decision="reject", officer="Billing Officer", reason="Attach the X-ray report"))
    assert state == ClaimState.PREAUTH_DRAFT
    with pytest.raises(InvalidTransition):
        transition(db, claim_id, ClaimState.SUBMITTED, "after rejection")


def test_edit_changes_the_amount_and_submits(db: Path, prompts: list[str]) -> None:
    claim_id = run_admission(load("case1.json"), db).claim_id
    edit = Decision(decision="edit", officer="Billing Officer", edits={"requested_amount": 95_000})
    assert decide(db, claim_id, edit) == ClaimState.SUBMITTED
    conn = connect(db)
    assert conn.execute("SELECT requested FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()[0] == 95_000
    record = [e for e in audit.events(db, claim_id) if e["action"] == "human_decision"]
    assert record[0]["actor"] == "human:Billing Officer"


def test_decision_only_for_a_waiting_claim(db: Path, prompts: list[str]) -> None:
    claim_id = run_admission(load("case1.json"), db).claim_id
    decide(db, claim_id, approve())
    with pytest.raises(ValueError, match="not waiting"):
        decide(db, claim_id, approve())


@pytest.mark.parametrize(
    "bad",
    [
        {"decision": "reject", "officer": "Billing Officer"},  # no reason
        {"decision": "edit", "officer": "Billing Officer", "edits": {}},  # nothing edited
        {"decision": "approve", "officer": " "},  # nobody
    ],
)
def test_incomplete_decisions_are_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(bad)


# ---- Masking --------------------------------------------------------------------------------


def test_masker_hides_and_restores() -> None:
    case = load("case2.json")
    masker = Masker.for_case(case)
    text = f"{case.patient.name} (Ramesh, {case.patient.patient_id}) phone +91 98450 12345, Aadhaar 2345 6789 0123, PAN ABCDE1234F"
    masked = masker.mask(text)
    for secret in ("Ramesh", "Iyer", case.patient.patient_id, "98450", "2345 6789 0123", "ABCDE1234F"):
        assert secret not in masked
    assert masker.restore(masked) == text


def test_no_patient_name_in_logged_prompts(db: Path, tmp_path: Path, prompts: list[str]) -> None:
    case = load("case2.json")
    # Put the name and a phone number in the admission note, which the pre-auth agent sends to the LLM.
    note = case.documents[0]
    note.fields["chief_complaint"] = f"{case.patient.name} (call 9845012345) reports knee pain for 3 years"
    note.file_path = str(tmp_path / f"{note.doc_id}.pdf")
    Path(note.file_path).write_bytes(render_pdf(note, case, None))
    result = run_admission(case, db)

    logged = [e["output"] for e in audit.events(db, result.claim_id) if e["action"] == "llm_call"]
    assert logged and prompts, "the pre-auth agent should have called the LLM"
    for secret in ("Ramesh", "Iyer", "9845012345"):
        assert all(secret not in p for p in prompts), "sent to the LLM unmasked"
        assert all(secret not in json.dumps(e) for e in logged), "logged unmasked"
    assert "[NAME_1]" in logged[0]["prompt"]
    # The reply is restored before anyone reads it.
    package = result.results["preauth"].data
    assert case.patient.name in package.justification and "[NAME_" not in package.justification
