"""Phase 6: hospital agents take a claim from ADMITTED to AWAITING_HUMAN."""

from pathlib import Path

import pytest

from backend import llm
from backend.agents.base import AgentResult
from backend.agents.hospital import estimator, intake
from backend.agents.orchestrator import (
    ADMISSION_AGENTS,
    InvalidTransition,
    open_claim,
    run_admission,
    transition,
)
from backend.db import audit
from backend.db.db import connect
from backend.models import train_cost, train_denial
from backend.models.predict import estimate_cost
from backend.schemas import AgentOutput, Case, ClaimState, Finding


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    """The cost estimator loads models from models/; train them there if this machine hasn't yet."""
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests don't depend on a running model; the pre-auth agent falls back to its template."""

    def unavailable(*args, **kwargs) -> str:
        raise llm.LLMUnavailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


def transitions(db: Path, claim_id: str) -> list[tuple[str, str]]:
    return [(e["output"]["from"], e["output"]["to"]) for e in audit.events(db, claim_id) if e["action"] == "transition"]


def test_case1_reaches_awaiting_human(db: Path) -> None:
    result = run_admission(load("case1.json"), db)
    assert result.state == ClaimState.AWAITING_HUMAN
    assert result.failed == []
    assert transitions(db, result.claim_id) == [
        ("ADMITTED", "PREAUTH_DRAFT"),
        ("PREAUTH_DRAFT", "RISK_CHECK"),
        ("RISK_CHECK", "AWAITING_HUMAN"),
    ]


@pytest.mark.parametrize("name", ["case1.json", "case2.json", "case3.json"])
def test_every_logged_output_validates_and_has_sources(db: Path, name: str) -> None:
    result = run_admission(load(name), db)
    outputs = [e for e in audit.events(db, result.claim_id) if e["action"] == "agent_output"]
    assert sorted(e["actor"] for e in outputs) == sorted(ADMISSION_AGENTS)
    for event in outputs:
        output = AgentOutput.model_validate(event["output"]["output"])
        assert output.agent == event["actor"] and output.claim_id == result.claim_id
        assert output.findings, f"{output.agent} reported no findings"
        assert all(f.source.strip() for f in output.findings)
        assert event["input_hash"]


def test_preauth_amount_comes_from_the_cost_model(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An LLM that tries to put a number in is ignored for the amount, and its text is dropped.
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "Please approve ₹9,99,999 for this patient.")
    case = load("case2.json")
    result = run_admission(case, db)
    package = result.results["preauth"].data
    assert package.requested_amount == estimate_cost(case).bill_typical
    assert package.justification_by == "template" and "9,99,999" not in package.justification
    conn = connect(db)
    assert conn.execute("SELECT requested FROM claims WHERE claim_id = ?", (result.claim_id,)).fetchone()[0] == package.requested_amount
    conn.close()


def test_llm_justification_used_when_it_has_no_amounts(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def fake(prompt: str, **kwargs) -> str:
        prompts.append(prompt)
        return "Acute appendicitis confirmed on ultrasound needs emergency surgery."

    monkeypatch.setattr(llm, "complete", fake)
    case = load("case1.json")
    package = run_admission(case, db).results["preauth"].data
    assert package.justification_by == "llm"
    assert case.patient.name not in prompts[0] and case.patient.patient_id not in prompts[0]


@pytest.mark.parametrize("name", ["case1.json", "case2.json", "case3.json"])
def test_document_reader_matches_ground_truth(name: str) -> None:
    case = load(name)
    reading = intake.run("CLM-TEST", case.documents).data
    for doc, read in zip(case.documents, reading.documents):
        for field, truth in doc.fields.items():
            if truth is None:
                assert field in read.not_found or field not in read.fields
            else:
                assert str(read.fields[field].value) == str(truth), f"{doc.doc_id} {field}"
                assert read.fields[field].page == 1


def test_case3_missing_reason_goes_to_a_person(db: Path) -> None:
    result = run_admission(load("case3.json"), db)
    reader = result.results["document_reader"].output
    assert reader.next == "human_review" and reader.confidence < intake.MIN_CONFIDENCE
    assert any("reason for admission" in f.issue for f in reader.findings)
    assert result.review_flags == ["document_reader: needs_review"]
    assert result.state == ClaimState.AWAITING_HUMAN


@pytest.mark.parametrize("name", ["case1.json", "case2.json", "case3.json"])
def test_coder_codes_each_case(db: Path, name: str) -> None:
    case = load(name)
    coding = run_admission(case, db).results["clinical_coder"].data
    assert coding.icd10 == case.admission.icd10


def test_case2_policy_cites_room_cap_and_matches_engine(db: Path) -> None:
    result = run_admission(load("case2.json"), db)
    review = result.results["policy"]
    assert review.data.assessment.payable == 185_800
    assert any(f.source == "Policy clause 4.2" and "Room rent capped" in f.issue for f in review.output.findings)


def test_invalid_output_retries_once_then_escalates(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def broken(claim_id: str, case: Case) -> AgentResult:
        calls.append(claim_id)
        no_source = Finding.model_construct(issue="Typical bill", evidence="₹80,400", source="")
        bad = AgentOutput.model_construct(agent=estimator.AGENT, claim_id=claim_id, decision="estimated",
                                          confidence=0.9, findings=[no_source], next="preauth")
        return AgentResult(output=bad, data=AgentOutput.model_construct())

    monkeypatch.setattr(estimator, "run", broken)
    result = run_admission(load("case1.json"), db)
    assert len(calls) == 2
    assert result.failed == [estimator.AGENT] and "preauth" not in result.results
    assert result.state == ClaimState.AWAITING_HUMAN
    actions = [(e["actor"], e["action"]) for e in audit.events(db, result.claim_id)]
    assert actions.count((estimator.AGENT, "agent_error")) == 2
    assert ("orchestrator", "escalate") in actions
    assert transitions(db, result.claim_id)[-1] == ("PREAUTH_DRAFT", "AWAITING_HUMAN")


def test_state_machine_rejects_skipping_states(db: Path) -> None:
    claim_id = open_claim(db, load("case1.json"))
    with pytest.raises(InvalidTransition):
        transition(db, claim_id, ClaimState.SUBMITTED, "skip the gate")
    conn = connect(db)
    assert conn.execute("SELECT state FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()[0] == "ADMITTED"
    conn.close()


def test_rerunning_a_case_opens_a_new_claim(db: Path) -> None:
    first = run_admission(load("case1.json"), db)
    second = run_admission(load("case1.json"), db)
    assert first.claim_id != second.claim_id
    assert second.state == ClaimState.AWAITING_HUMAN
