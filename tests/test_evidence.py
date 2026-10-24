"""Refinement of phases 7-8: appeals and insurer decisions rest on evidence, not on a document merely existing."""

from pathlib import Path

import pytest

from backend import llm
from backend.agents import orchestrator
from backend.agents.approval import Decision, decide
from backend.agents.hospital import appeal
from backend.agents.hospital.intake import Reading, read_document
from backend.agents.insurer import medical_review
from backend.agents.orchestrator import pending_draft, run_admission
from backend.db import audit
from backend.evidence.clinical import Verdict, assess_admission_necessity, check_citations, findings_in
from backend.exchange import messages as exchange
from backend.models import train_cost, train_denial
from backend.negotiation import play_round
from backend.schemas import Appeal, Approve, Case, ClaimState, Deduction, Document, DocumentType, PartialApprove, Stage
from data.generator.render import render_pdf

OFFICER = "Billing Officer"
NECESSITY = "Medical necessity: reason for admission not documented"


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


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


def with_records(case: Case, tmp_path: Path, **fields_by_type: dict) -> Case:
    """The case with some clinical records rewritten (and re-rendered) to say something else."""
    docs = []
    for doc in case.documents:
        if doc.type.value in fields_by_type:
            doc = doc.model_copy(update={"fields": fields_by_type[doc.type.value], "file_path": str(tmp_path / f"{doc.doc_id}.pdf")})
            Path(doc.file_path).write_bytes(render_pdf(doc, case, None))
        docs.append(doc)
    return case.model_copy(update={"documents": docs})


def drive(db: Path, case: Case, gates: int = 8) -> str:
    """Approve every draft and move each decided stage on, until the claim needs a real decision."""
    result = run_admission(case, db)
    claim_id = result.claim_id
    for _ in range(gates):
        state = orchestrator.claim_state(db, claim_id)
        if state == ClaimState.AWAITING_HUMAN and pending_draft(db, claim_id) is not None:
            decide(db, claim_id, Decision(decision="approve", officer=OFFICER))
            play_round(db, claim_id, case)
        elif state in (ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED):
            if orchestrator.next_stage(db, claim_id) == ClaimState.IN_TREATMENT:
                orchestrator.discharge(db, claim_id, case)
        else:
            break
    return claim_id


IRRELEVANT_NOTE = {
    "chief_complaint": "Loose stools for 2 days",
    "provisional_diagnosis": "Acute gastroenteritis",
    "planned_treatment": "Monitoring",
}
NORMAL_LABS = {"test": "Serum electrolytes and renal function", "finding": "Within normal limits"}
AGAINST_NOTE = {
    "chief_complaint": "Loose stools for 2 days",
    "provisional_diagnosis": "Acute gastroenteritis",
    "vitals": "Vitals stable, no dehydration",
    "assessment": "Could be managed as an outpatient; admitted at the patient's request",
}


# ---- 1. Relevant evidence supports an appeal --------------------------------------------------


def test_relevant_evidence_supports_the_appeal_and_the_insurer_checks_it(db: Path) -> None:
    case = load("case3.json")
    claim_id = drive(db, case)
    messages = exchange.thread(db, claim_id)
    letter = next(m for m in messages if isinstance(m, Appeal))
    assert set(letter.cited_documents) <= {"ADM-0003-D1", "ADM-0003-D2", "ADM-0003-D3"}  # only records with findings
    assert "ADM-0003-D1" in letter.cited_documents and "ADM-0003-D4" not in letter.cited_documents  # the bill shows nothing clinical
    assert 'ADM-0003-D1 p.1: "Vitals: BP 92/60, pulse 112"' in letter.argument  # the exact passage, with page
    assert isinstance(messages[-1], Approve)
    review = [e for e in audit.events(db, claim_id) if e["actor"] == "tpa_medical_reviewer"][-1]["output"]
    assert review["data"]["necessity"]["verdict"] == Verdict.SUPPORTED
    assert any(f["issue"] == "Admission justified by the cited records" for f in review["output"]["findings"])


# ---- 2. Existing but irrelevant evidence does not ---------------------------------------------


def test_irrelevant_records_are_not_argued_and_the_cut_goes_to_a_person(db: Path, tmp_path: Path) -> None:
    case = with_records(load("case3.json"), tmp_path, admission_note=IRRELEVANT_NOTE, lab_report=NORMAL_LABS)
    claim_id = drive(db, case)
    assert not any(isinstance(m, Appeal) for m in exchange.thread(db, claim_id))  # nothing manufactured
    assert orchestrator.claim_state(db, claim_id) == ClaimState.AWAITING_HUMAN
    assert pending_draft(db, claim_id) is None
    out = [e for e in audit.events(db, claim_id) if e["actor"] == "appeal"][-1]["output"]
    assert out["output"]["decision"] == "needs_person"
    assert out["data"]["evidence"]["verdict"] in (Verdict.UNSUPPORTED, Verdict.INSUFFICIENT)
    assert out["data"]["held_back"][0]["clause"] == NECESSITY


def test_insurer_is_not_persuaded_by_citing_an_irrelevant_record(tmp_path: Path) -> None:
    case = with_records(load("case3.json"), tmp_path, admission_note=IRRELEVANT_NOTE, lab_report=NORMAL_LABS)
    necessity = assess_admission_necessity([d for d in case.documents if d.doc_id in ("ADM-0003-D1", "ADM-0003-D2")])
    review = medical_review.run("CLM-T", case, case.documents, necessity, Stage.DISCHARGE).data
    assert review.stay_unjustified  # the document exists and was cited, but shows nothing


def test_a_diagnosis_name_alone_is_not_a_finding() -> None:
    assert findings_in("Provisional diagnosis: Acute gastroenteritis with dehydration") == []
    assert findings_in("Planned treatment: Total knee replacement") == []
    assert findings_in("Patient denies vomiting, no dehydration") == [("No dehydration", "against")]


# ---- 3. Contradictory evidence escalates -------------------------------------------------------


def test_contradictory_records_escalate(db: Path, tmp_path: Path) -> None:
    case = with_records(load("case3.json"), tmp_path, admission_note=AGAINST_NOTE)
    assessment = assess_admission_necessity(case.documents)
    assert assessment.verdict == Verdict.CONTRADICTORY  # abnormal labs, but the note argues against admission
    claim_id = drive(db, case)
    assert not any(isinstance(m, Appeal) for m in exchange.thread(db, claim_id))
    assert orchestrator.claim_state(db, claim_id) == ClaimState.AWAITING_HUMAN
    out = [e for e in audit.events(db, claim_id) if e["actor"] == "appeal"][-1]["output"]
    assert out["data"]["evidence"]["verdict"] == Verdict.CONTRADICTORY


# ---- 4. Missing or foreign-claim citations are rejected ---------------------------------------


def test_missing_foreign_and_unsent_citations_are_rejected() -> None:
    case = load("case3.json")
    cited = ["ADM-0003-D1", "ADM-0003-D9", "ADM-0001-D2", "ADM-0003-D2"]
    valid, problems = check_citations(cited, case.documents, "ADM-0003", sent={"ADM-0003-D1"})
    assert [d.doc_id for d in valid] == ["ADM-0003-D1"]
    assert {(p.doc_id, p.problem) for p in problems} == {
        ("ADM-0003-D9", "not_in_claim"), ("ADM-0001-D2", "other_admission"), ("ADM-0003-D2", "not_sent"),
    }
    assessment = assess_admission_necessity([], problems=problems)
    assert assessment.verdict == Verdict.INSUFFICIENT and not assessment.supported


def test_an_unreadable_scan_is_never_taken_as_support(tmp_path: Path) -> None:
    import fitz

    scan = tmp_path / "scan.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(scan)
    doc = Document(doc_id="ADM-0003-D1", type=DocumentType.ADMISSION_NOTE, file_path=str(scan))
    assessment = assess_admission_necessity([doc])
    assert assessment.verdict == Verdict.INSUFFICIENT
    assert [p.problem for p in assessment.problems] == ["unreadable"]


# ---- 5. A valid policy deduction is accepted ---------------------------------------------------


def test_a_deduction_the_policy_supports_is_accepted_not_appealed() -> None:
    case = load("case3.json")
    reading = Reading(documents=[read_document(d) for d in case.documents])
    from backend.rules.engine import assess

    assessment = assess(case)
    cuts = [Deduction(item=d.item, amount=d.amount, clause=d.clause_id) for d in assessment.deductions]
    decision = PartialApprove(claim_id="CLM-T", stage=Stage.DISCHARGE, round=1, approved_amount=assessment.payable, deductions=cuts)
    result = appeal.run("CLM-T", decision, case, reading, assessment.billed, 2)
    assert result.output.decision == "accept_decision" and result.data.appeal is None


def test_a_policy_dispute_is_argued_from_the_clause_without_clinical_documents() -> None:
    case = load("case2.json")
    reading = Reading(documents=[read_document(d) for d in case.documents])
    # The insurer also cut medicines under the room clause, which the clause excludes (TPA-C's habit).
    decision = PartialApprove(
        claim_id="CLM-T", stage=Stage.PREAUTH, round=1, approved_amount=175_800,
        deductions=[Deduction(item="Consumables", amount=7_000, clause="Annexure II"), Deduction(item="Linked charges", amount=57_200, clause="4.2")],
    )
    result = appeal.run("CLM-T", decision, case, reading, 240_000, 2)
    letter = result.data.appeal
    assert letter is not None and letter.cited_documents == []
    assert result.data.contested[0].kind == "policy" and result.data.disputed_total == 10_000
    assert "Implants and medicines are not subject to this proportion" in letter.argument


# ---- 6/7. Round limit and approval of revised content -----------------------------------------


def test_supported_evidence_does_not_skip_the_gate_or_the_round_limit(db: Path) -> None:
    case = load("case3.json")
    claim_id = drive(db, case, gates=0)  # admitted, nothing approved
    assert exchange.thread(db, claim_id) == []
    rounds = [m.round for m in exchange.thread(db, drive(db, case))]
    assert max(rounds) <= 3
