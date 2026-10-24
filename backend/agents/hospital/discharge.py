"""Discharge agent: puts the final bill and discharge summary into the final package (design section 4).

The package claims the final bill, with a deduction preview from the rules engine so the billing
officer and the patient know before the insurer answers what the policy will pay. A discharge
summary that is missing, or doesn't say why admission was needed, is flagged: insurers cut stays
for it. Every final package goes to a person (gate).
"""

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.agents.hospital.intake import Reading
from backend.rules.engine import RuleDeduction, assess
from backend.schemas import AgentOutput, Case, DocumentType, Finding, Stage, Submit
from data.generator.admissions import ADMISSION_REASON

AGENT = "discharge"


class FinalPackage(BaseModel):
    billed: int
    expected_payable: int  # rules engine
    patient_share: int
    deductions: list[RuleDeduction]  # the preview, each citing its clause
    documents: list[str]
    summary_gap: str | None  # what is wrong with the discharge summary, if anything
    submit: Submit


def run(claim_id: str, case: Case, reading: Reading) -> AgentResult:
    assessment = assess(case)
    summary = reading.get(DocumentType.DISCHARGE_SUMMARY)
    if summary is None:
        gap = "No discharge summary in the claim"
    elif not reading.value(DocumentType.DISCHARGE_SUMMARY, ADMISSION_REASON):
        gap = "The discharge summary doesn't say why admission was needed"
    else:
        gap = None
    documents = [doc.doc_id for doc in reading.documents]
    submit = Submit(
        claim_id=claim_id,
        stage=Stage.DISCHARGE,
        round=1,
        package={
            "billed": assessment.billed,
            "expected_payable": assessment.payable,
            "deduction_preview": [d.model_dump(mode="json") for d in assessment.deductions],
        },
        documents=documents,
        requested_amount=assessment.billed,  # the final bill; the insurer prices it against the policy
    )
    package = FinalPackage(
        billed=assessment.billed,
        expected_payable=assessment.payable,
        patient_share=assessment.patient_share,
        deductions=assessment.deductions,
        documents=documents,
        summary_gap=gap,
        submit=submit,
    )

    findings = [
        Finding(issue="Final bill", evidence=f"₹{assessment.billed:,} over {len(case.bill_lines)} bill lines", source="Final bill"),
        Finding(
            issue="Deduction preview",
            evidence=f"Insurer expected to pay ₹{assessment.payable:,}; patient pays ₹{assessment.patient_share:,}",
            source="Rules engine",
        ),
    ]
    findings += [
        Finding(issue=d.item, evidence=f"{d.reason}: ₹{d.amount:,} not payable", source=f"Policy clause {d.clause_id}")
        for d in assessment.deductions
    ]
    if gap:
        source = summary.source() if summary else "Document checklist"
        findings.append(Finding(issue="Discharge summary gap", evidence=f"{gap}; insurers cut the stay for this", source=source))
    else:
        findings.append(Finding(issue="Discharge summary", evidence="States why admission was needed", source=summary.source()))
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="review_gaps" if gap else "ready_for_review",
            confidence=0.6 if gap else 0.9,
            findings=findings,
            next="human_approval",  # a final package always goes to a person
        ),
        data=package,
    )
