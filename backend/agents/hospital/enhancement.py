"""Enhancement agent: watches the running bill and asks for more when it outgrows the approval (design section 4).

It triggers when the insurer would owe more on the running bill (priced by the rules engine) than
it has approved so far. The request is for that payable amount, and the reasons are the charges
added since the last submission, each cited by bill line. Every request goes to a person (gate).
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.rules.engine import assess
from backend.schemas import AgentOutput, Case, Finding, Stage, Submit

AGENT = "enhancement"
SOURCE = "Rules engine, on the running bill"


class EnhancementRequest(BaseModel):
    billed: int  # running bill so far
    payable: int  # what the policy pays on it
    approved: int  # what the insurer has approved so far
    new_lines: list[str]  # bill line ids added since the last submission
    submit: Submit | None  # None: the bill is within the approval, or no enhancement round is left


def run(claim_id: str, case: Case, approved: int, last_sent: Case | None, enhancement_used: bool) -> AgentResult:
    assessment = assess(case)
    sent_ids = {line.line_id for line in last_sent.bill_lines} if last_sent else set()
    new = [line for line in case.bill_lines if line.line_id not in sent_ids]
    request = EnhancementRequest(
        billed=assessment.billed, payable=assessment.payable, approved=approved, new_lines=[l.line_id for l in new], submit=None
    )
    findings = [
        Finding(
            issue="Running bill",
            evidence=f"₹{assessment.billed:,} billed; the policy pays ₹{assessment.payable:,}; ₹{approved:,} approved so far",
            source=SOURCE,
        )
    ]
    if assessment.payable <= approved:
        findings.append(Finding(issue="Within the approval", evidence="No enhancement needed yet", source=SOURCE))
        return _result(claim_id, "within_approval", findings, "none", request)

    findings += [Finding(issue="New charge", evidence=f"{line.description}: ₹{line.amount:,}", source=f"Bill line {line.line_id}") for line in new]
    if enhancement_used:
        findings.append(Finding(issue="Enhancement already used", evidence="This claim has had its enhancement round; a person decides", source="Claim exchange"))
        return _result(claim_id, "needs_person", findings, HUMAN, request)

    request.submit = Submit(
        claim_id=claim_id,
        stage=Stage.ENHANCEMENT,
        round=1,
        package={
            "billed": assessment.billed,
            "expected_payable": assessment.payable,
            "approved_so_far": approved,
            "new_charges": [line.model_dump(mode="json") for line in new],
            "deductions": [d.model_dump(mode="json") for d in assessment.deductions],
        },
        documents=[doc.doc_id for doc in case.documents],
        requested_amount=assessment.payable,
    )
    findings.append(
        Finding(issue="Amount requested", evidence=f"₹{assessment.payable:,}, up from ₹{approved:,}", source=SOURCE)
    )
    return _result(claim_id, "request_enhancement", findings, "human_approval", request)  # always a person (gate)


def _result(claim_id: str, decision: str, findings: list[Finding], next_step: str, request: EnhancementRequest) -> AgentResult:
    return AgentResult(
        output=AgentOutput(agent=AGENT, claim_id=claim_id, decision=decision, confidence=1.0, findings=findings, next=next_step),
        data=request,
    )
