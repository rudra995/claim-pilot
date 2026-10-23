"""Pre-auth agent: puts the four earlier outputs into one pre-auth package for a person to approve.

The amount requested is the cost model's typical bill; the expected deductions come from the
rules engine. The LLM writes only the clinical justification, and is given no amounts. If it is
unavailable, or its text mentions money, a fixed template is used instead.
"""

import re
from typing import Literal

from pydantic import BaseModel

from backend import llm
from backend.agents.base import AgentResult
from backend.agents.hospital.coder import Coding
from backend.agents.hospital.estimator import SOURCE as COST_SOURCE
from backend.agents.hospital.estimator import Estimate
from backend.agents.hospital.intake import Reading
from backend.agents.hospital.policy import PolicyReview
from backend.rules.engine import RuleDeduction
from backend.schemas import Admission, AgentOutput, DocumentType, Finding

AGENT = "preauth"

_SYSTEM = (
    "You write the medical justification paragraph of a hospital's cashless pre-authorisation request "
    "to an Indian health insurer. Use only the facts given. Do not mention any amounts, prices or "
    "rupees. Three or four plain sentences."
)
_MONEY = re.compile(r"₹|\brs\.?\s*\d|\brupee|\binr\b", re.IGNORECASE)


class PreAuthPackage(BaseModel):
    claim_id: str
    admission_id: str
    policy_id: str
    tpa_id: str
    procedure: str
    diagnosis: str
    icd10: str
    requested_amount: int  # the cost model's typical bill
    high_estimate: int  # the cost model's 90th percentile, for a likely enhancement
    expected_stay_days: int
    expected_payable: int  # rules engine, on the itemised bill
    expected_deductions: list[RuleDeduction]
    documents: list[str]  # doc ids attached
    justification: str
    justification_by: Literal["llm", "template"]
    review_flags: list[str]  # earlier agents that asked for a person


def run(
    claim_id: str,
    admission: Admission,
    reading: Reading,
    coding: Coding,
    policy: PolicyReview,
    estimate: Estimate,
    review_flags: list[str],
) -> AgentResult:
    facts = _clinical_facts(reading, coding)
    justification, written_by = _justification(facts)
    assessment = policy.assessment
    package = PreAuthPackage(
        claim_id=claim_id,
        admission_id=admission.admission_id,
        policy_id=admission.policy_id,
        tpa_id=admission.tpa_id,
        procedure=admission.procedure,
        diagnosis=coding.diagnosis,
        icd10=coding.icd10,
        requested_amount=estimate.bill_typical,
        high_estimate=estimate.bill_high,
        expected_stay_days=estimate.stay_typical,
        expected_payable=assessment.payable,
        expected_deductions=assessment.deductions,
        documents=[doc.doc_id for doc in reading.documents],
        justification=justification,
        justification_by=written_by,
        review_flags=review_flags,
    )

    findings = [
        Finding(issue="Amount requested", evidence=f"₹{package.requested_amount:,} (typical bill)", source=f"{COST_SOURCE}, 50th percentile"),
        Finding(issue="Diagnosis", evidence=f"{coding.icd10} {coding.diagnosis}", source=coding.source),
        Finding(
            issue="Expected deductions",
            evidence=f"Deductions found: {len(assessment.deductions)}; insurer expected to pay ₹{assessment.payable:,} of ₹{assessment.billed:,}",
            source="Rules engine",
        ),
        Finding(issue="Documents attached", evidence=", ".join(package.documents) or "None", source="Document reader"),
    ]
    findings += [Finding(issue="Needs a person", evidence=flag, source="Orchestrator") for flag in review_flags]
    for doc_type in (DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT):
        if reading.get(doc_type) is None:
            findings.append(Finding(issue=f"No {doc_type.value.replace('_', ' ')} attached", evidence="Pre-auth usually needs it", source="Document reader"))

    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="review_flags" if review_flags else "ready_for_review",
            confidence=0.6 if review_flags else 0.9,
            findings=findings,
            next="human_approval",  # a pre-auth always goes to a person (design section 4)
        ),
        data=package,
    )


def _clinical_facts(reading: Reading, coding: Coding) -> dict[str, str]:
    """Clinical fields only: no names, ID numbers or amounts."""
    wanted = [
        ("Chief complaint", DocumentType.ADMISSION_NOTE, "chief_complaint"),
        ("Planned treatment", DocumentType.ADMISSION_NOTE, "planned_treatment"),
        ("Investigation", DocumentType.LAB_REPORT, "test"),
        ("X-ray finding", DocumentType.XRAY_REPORT, "finding"),
    ]
    facts = {"Diagnosis": f"{coding.diagnosis} ({coding.icd10})"}
    for label, doc_type, name in wanted:
        if value := reading.value(doc_type, name):
            facts[label] = str(value)
    return facts


def _justification(facts: dict[str, str]) -> tuple[str, Literal["llm", "template"]]:
    prompt = "\n".join(f"{label}: {value}" for label, value in facts.items())
    try:
        text = llm.complete(prompt, system=_SYSTEM, seed=7, temperature=0.2).strip()
    except llm.LLMUnavailable:
        text = ""
    if text and not _MONEY.search(text):
        return text, "llm"
    return _template(facts), "template"


def _template(facts: dict[str, str]) -> str:
    sentences = [f"The patient presented with {facts['Chief complaint'].rstrip('.').lower()}."] if "Chief complaint" in facts else []
    sentences.append(f"Diagnosis: {facts['Diagnosis']}.")
    if "Investigation" in facts:
        sentences.append(f"Supporting investigation: {facts['Investigation'].rstrip('.')}.")
    if "X-ray finding" in facts:
        sentences.append(f"X-ray: {facts['X-ray finding'].rstrip('.')}.")
    if "Planned treatment" in facts:
        sentences.append(f"Planned treatment: {facts['Planned treatment'].rstrip('.')}, which needs inpatient admission.")
    return " ".join(sentences)
