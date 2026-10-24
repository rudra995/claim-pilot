"""Phase 9: the rest of the stay. Enhancement, discharge, settlement, reconciliation, and the patient's room advice."""

from pathlib import Path

import pytest

from backend import llm
from backend.agents.approval import Decision, decide
from backend.agents.hospital import patient
from backend.agents.orchestrator import (
    InvalidTransition,
    advise_patient,
    claim_state,
    discharge,
    next_stage,
    pending_draft,
    reconcile,
    run_admission,
    settle,
    transition,
    watch_bill,
)
from backend.db import audit
from backend.db.db import connect
from backend.exchange import messages as exchange
from backend.models import train_cost, train_denial
from backend.negotiation import play_round
from backend.schemas import MAX_ROUNDS, Case, ClaimState, Stage, Submit

OFFICER = "Billing Officer"


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args, **kwargs) -> str:
        raise llm.LLMUnavailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


def approve_and_play(db: Path, claim_id: str, case: Case, rounds: int = MAX_ROUNDS) -> ClaimState:
    """Approve every drafted message and play it with the insurer, as a billing officer would."""
    for _ in range(rounds):
        if claim_state(db, claim_id) != ClaimState.AWAITING_HUMAN or pending_draft(db, claim_id) is None:
            break
        decide(db, claim_id, Decision(decision="approve", officer=OFFICER))
        play_round(db, claim_id, case)
    return claim_state(db, claim_id)


def admitted_and_treating(db: Path, case: Case) -> str:
    """Pre-auth approved and the patient in treatment."""
    claim_id = run_admission(case, db).claim_id
    assert approve_and_play(db, claim_id, case) in (ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED)
    assert next_stage(db, claim_id) == ClaimState.IN_TREATMENT
    return claim_id


def with_bill(case: Case, n_lines: int) -> Case:
    return case.model_copy(update={"bill_lines": case.bill_lines[:n_lines]})


def approved(db: Path, claim_id: str) -> int:
    conn = connect(db)
    try:
        return conn.execute("SELECT approved FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()["approved"]
    finally:
        conn.close()


# ---- Patient agent: room advisor ---------------------------------------------------------------


def test_case2_room_advisor_says_the_3000_room_saves_47200() -> None:
    result = patient.run("CLM-TEST", load("case2.json"))
    note = result.data
    assert note.recommended is not None
    assert (note.recommended.rate, note.recommended.patient_pays, note.recommended.saving) == (3_000, 7_000, 47_200)
    current = next(o for o in note.options if o.current)
    assert (current.rate, current.patient_pays) == (5_000, 54_200)
    assert "saving about ₹47,200" in note.note and "₹54,200" in note.note
    assert all(f.source.startswith("Rules engine (what_if_room)") for f in result.output.findings)
    assert result.output.next == "human_approval"  # staff check the note before the patient gets it


def test_room_within_the_cap_needs_no_switch() -> None:
    note = patient.run("CLM-TEST", load("case1.json")).data  # ₹4,000 room, ₹5,000 cap
    assert note.recommended is None
    assert "within your policy's room limit" in note.note


def test_patient_note_is_logged_for_staff(db: Path) -> None:
    case = load("case2.json")
    claim_id = run_admission(case, db).claim_id
    assert advise_patient(db, claim_id, case).data.recommended.saving == 47_200
    assert [e["actor"] for e in audit.events(db, claim_id) if e["action"] == "agent_output"][-1] == "patient"


# ---- Enhancement ------------------------------------------------------------------------------


def test_bill_pushed_past_the_approval_triggers_an_enhancement(db: Path) -> None:
    case = load("case1.json")  # pre-auth asks the cost model's typical bill; the full bill is ₹98,000
    claim_id = admitted_and_treating(db, with_bill(case, 2))
    first_approval = approved(db, claim_id)
    assert first_approval == 47_000  # the insurer priced the bill it was sent: room and surgeon fees

    # Room and surgeon fees so far: within the approval, nothing to ask for.
    result = watch_bill(db, claim_id, with_bill(case, 2))
    assert result.results["enhancement"].output.decision == "within_approval"
    assert claim_state(db, claim_id) == ClaimState.IN_TREATMENT

    # The whole bill: past the approval, so an enhancement is drafted and waits for a person.
    result = watch_bill(db, claim_id, case)
    request = result.results["enhancement"].data
    assert result.state == ClaimState.AWAITING_HUMAN
    assert request.payable == 98_000 and request.approved == first_approval
    assert request.new_lines == ["ADM-0001-L3", "ADM-0001-L4", "ADM-0001-L5", "ADM-0001-L6"]
    submit = pending_draft(db, claim_id)["message"]
    assert submit["stage"] == Stage.ENHANCEMENT and submit["requested_amount"] == 98_000
    assert "denial_risk" in result.results  # checked before a person sees it

    # Approved and sent: the insurer raises the approval, and treatment goes on.
    assert approve_and_play(db, claim_id, case) == ClaimState.APPROVED
    assert approved(db, claim_id) == 98_000
    assert next_stage(db, claim_id) == ClaimState.IN_TREATMENT


def test_second_enhancement_goes_to_a_person(db: Path) -> None:
    case = load("case1.json")
    claim_id = admitted_and_treating(db, with_bill(case, 2))
    watch_bill(db, claim_id, with_bill(case, 5))
    approve_and_play(db, claim_id, with_bill(case, 5))
    next_stage(db, claim_id)
    result = watch_bill(db, claim_id, case)  # the bill grows again; the enhancement stage is used
    assert result.state == ClaimState.AWAITING_HUMAN
    assert result.results["enhancement"].output.decision == "needs_person"
    assert pending_draft(db, claim_id) is None


def test_enhancement_only_while_in_treatment(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    with pytest.raises(InvalidTransition):
        watch_bill(db, claim_id, case)


# ---- Discharge, settlement, reconciliation -------------------------------------------------------


def test_case1_discharge_is_final_approved_settled_and_closed(db: Path) -> None:
    case = load("case1.json")
    claim_id = admitted_and_treating(db, case)
    result = discharge(db, claim_id, case)
    package = result.results["discharge"].data
    assert (package.billed, package.expected_payable, package.summary_gap) == (98_000, 98_000, None)
    assert result.state == ClaimState.AWAITING_HUMAN

    assert approve_and_play(db, claim_id, case) == ClaimState.APPROVED
    assert next_stage(db, claim_id) == ClaimState.FINAL_APPROVED
    settle(db, claim_id, paid=98_000, reference="UTR-0001")
    result = reconcile(db, claim_id, case)
    report = result.results["reconciliation"].data
    assert (report.to_chase, report.recoverable_total) == (0, 0)
    assert claim_state(db, claim_id) == ClaimState.CLOSED


def test_case3_reconciliation_separates_valid_and_recoverable(db: Path) -> None:
    case = load("case3.json")
    claim_id = admitted_and_treating(db, case)
    result = discharge(db, claim_id, case)
    assert result.results["discharge"].data.summary_gap == "The discharge summary doesn't say why admission was needed"

    # TPA-B cuts the stay again at discharge. The patient is waiting to go home, so the officer
    # accepts the cut instead of appealing, and the hospital chases it afterwards.
    decide(db, claim_id, Decision(decision="approve", officer=OFFICER))
    play_round(db, claim_id, case)
    assert claim_state(db, claim_id) == ClaimState.AWAITING_HUMAN  # appeal drafted
    assert decide(db, claim_id, Decision(decision="accept", officer=OFFICER)) == ClaimState.PARTIALLY_APPROVED
    assert next_stage(db, claim_id) == ClaimState.FINAL_APPROVED

    paid = approved(db, claim_id)
    settle(db, claim_id, paid=paid, reference="UTR-0003")
    result = reconcile(db, claim_id, case)
    report = result.results["reconciliation"].data
    by_clause = {c.clause: c for c in report.clauses}

    copay = by_clause["6.1"]
    assert copay.valid == copay.insurer_cut and copay.recoverable == 0  # the senior co-pay is in the policy
    necessity = by_clause["Medical necessity: reason for admission not documented"]
    assert necessity.policy_cut == 0 and necessity.recoverable == necessity.insurer_cut == 13_500  # room + nursing
    assert report.valid_total + report.recoverable_total == report.billed - paid
    assert report.expected == 38_250  # ₹42,500 less the 10% co-pay, from the engine
    assert report.to_chase == 38_250 - paid  # the co-pay on the recovered stay is allowed for
    assert result.review_flags == ["reconciliation: recover_gap"]  # a person chases it
    assert claim_state(db, claim_id) == ClaimState.SETTLED  # not closed while money is owed


def test_paid_short_of_the_approval_is_recoverable(db: Path) -> None:
    case = load("case1.json")
    claim_id = admitted_and_treating(db, case)
    discharge(db, claim_id, case)
    approve_and_play(db, claim_id, case)
    next_stage(db, claim_id)
    settle(db, claim_id, paid=90_000, reference="UTR-0004")
    report = reconcile(db, claim_id, case).results["reconciliation"].data
    assert (report.short_paid, report.recoverable_total, report.to_chase) == (8_000, 8_000, 8_000)


def test_final_approval_and_settlement_need_their_evidence(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    approve_and_play(db, claim_id, case)  # pre-auth approved
    with pytest.raises(InvalidTransition, match="discharge decision"):
        transition(db, claim_id, ClaimState.FINAL_APPROVED, "skipping discharge")
    next_stage(db, claim_id)
    discharge(db, claim_id, case)
    approve_and_play(db, claim_id, case)
    next_stage(db, claim_id)
    with pytest.raises(InvalidTransition, match="payment advice"):
        transition(db, claim_id, ClaimState.SETTLED, "no payment")


def test_rejected_final_package_goes_back_to_discharge(db: Path) -> None:
    case = load("case1.json")
    claim_id = admitted_and_treating(db, case)
    discharge(db, claim_id, case)
    state = decide(db, claim_id, Decision(decision="reject", officer=OFFICER, reason="Add the final pharmacy bill"))
    assert state == ClaimState.DISCHARGE_DRAFT


def test_each_stage_opens_its_own_thread(db: Path) -> None:
    case = load("case1.json")
    claim_id = admitted_and_treating(db, with_bill(case, 2))
    watch_bill(db, claim_id, case)
    approve_and_play(db, claim_id, case)
    next_stage(db, claim_id)
    discharge(db, claim_id, case)
    approve_and_play(db, claim_id, case)
    submits = [m for m in exchange.thread(db, claim_id) if isinstance(m, Submit)]
    assert [s.stage for s in submits] == [Stage.PREAUTH, Stage.ENHANCEMENT, Stage.DISCHARGE]
    assert all("case" in s.package and s.stage.value in s.package for s in submits)
