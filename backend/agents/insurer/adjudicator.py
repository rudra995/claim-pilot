"""TPA adjudicator: decides the round and sets the approved amount (design section 5).

In order: a fraud flag that rules the claim out denies it; missing documents or an unusual stay
become a query; otherwise the rules engine prices the claim with the TPA's own habits applied,
and every deduction cites its clause. The insurer never approves more than was requested. In the
last round there is no query left to ask, so a gap becomes a denial.
"""

from typing import Literal

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.agents.insurer.fraud import FraudScreen
from backend.agents.insurer.medical_review import Gap, MedicalReview
from backend.rules.engine import RuleDeduction, assess
from backend.schemas import MAX_ROUNDS, AgentOutput, Case, Deduction, Finding, RuleType
from data.generator.tpa_profiles import STAY_CHARGES, TPA_PROFILES

AGENT = "tpa_adjudicator"
ENGINE_SOURCE = "Rules engine, with the TPA's profile applied"
NECESSITY_CLAUSE = "Medical necessity: reason for admission not documented"
CODING_CLAUSE = "Claim coding: the diagnosis code must match the treatment"
DUPLICATE_CLAUSE = "Duplicate claim"
DOCUMENTS_CLAUSE = "Claim documents: requested records not received"


class Ruling(BaseModel):
    kind: Literal["approve", "partial", "deny", "query"]
    final: bool  # last round: the decision closes the stage
    approved_amount: int
    deductions: list[Deduction]
    reason: str | None = None  # for a denial
    clause: str | None = None  # for a denial
    gaps: list[Gap] = []  # for a query


def run(claim_id: str, case: Case, requested: int, review: MedicalReview, screen: FraudScreen, round: int) -> AgentResult:
    final = round >= MAX_ROUNDS
    billed = sum(line.amount for line in case.bill_lines)
    gaps = review.gaps + [Gap(missing="Daily clinical notes for the stay", reason=flag.detail) for flag in screen.of("long_stay")]

    if flags := screen.of("upcoding") + screen.of("duplicate"):
        clause = CODING_CLAUSE if flags[0].kind == "upcoding" else DUPLICATE_CLAUSE
        ruling = _deny(flags[0].detail, clause, billed, final)
    elif gaps and not final:
        ruling = Ruling(kind="query", final=False, approved_amount=0, deductions=[], gaps=gaps)
    elif gaps:
        ruling = _deny("Not received after three rounds: " + "; ".join(gap.missing for gap in gaps), DOCUMENTS_CLAUSE, billed, final)
    else:
        ruling = _price(case, requested, review, final)

    findings = [Finding(issue=d.item, evidence=f"₹{d.amount:,} not paid", source=f"Policy clause {d.clause}") for d in ruling.deductions]
    if ruling.kind == "query":
        findings += [Finding(issue=f"Ask for: {gap.missing}", evidence=gap.reason, source="TPA medical reviewer") for gap in gaps]
    elif ruling.kind == "deny":
        findings.append(Finding(issue="Denied", evidence=ruling.reason or "", source=ruling.clause or ""))
    else:
        capped = " (capped at the amount requested)" if ruling.approved_amount == requested else ""
        findings.append(Finding(issue="Approved amount", evidence=f"₹{ruling.approved_amount:,} of ₹{billed:,} billed{capped}", source=ENGINE_SOURCE))
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision=ruling.kind,
            confidence=1.0,
            findings=findings,
            next="tpa_query_writer" if ruling.kind == "query" else "reply",
        ),
        data=ruling,
    )


def _price(case: Case, requested: int, review: MedicalReview, final: bool) -> Ruling:
    profile = TPA_PROFILES[case.admission.tpa_id]
    tpa_case = case.model_copy(update={"policy": case.policy.model_copy(update={"rules": profile.apply(case.policy.rules)})})
    deductions: list[Deduction] = []
    if review.stay_unjustified:  # pay the treatment, not the stay
        stay = [line for line in case.bill_lines if line.category in STAY_CHARGES]
        deductions += [Deduction(item=f"{line.description}: stay charges not paid", amount=line.amount, clause=NECESSITY_CLAUSE) for line in stay]
        tpa_case = tpa_case.model_copy(update={"bill_lines": [line for line in case.bill_lines if line not in stay]})

    assessment = assess(tpa_case)
    deductions += [_deduction(d) for d in assessment.deductions]
    if assessment.payable == 0:
        cut = assessment.deductions[0]
        return _deny(cut.reason, cut.clause_id, assessment.billed, final)

    rule_types = {rule.rule_id: rule.type for rule in case.policy.rules}
    cuts = review.stay_unjustified or any(rule_types.get(d.rule_id) != RuleType.CO_PAY for d in assessment.deductions)
    return Ruling(kind="partial" if cuts else "approve", final=final, approved_amount=min(assessment.payable, requested), deductions=deductions)


def _deduction(d: RuleDeduction) -> Deduction:
    return Deduction(item=f"{d.item}: {d.reason}", amount=d.amount, clause=d.clause_id)


def _deny(reason: str, clause: str, billed: int, final: bool) -> Ruling:
    return Ruling(
        kind="deny",
        final=final,
        approved_amount=0,
        deductions=[Deduction(item=f"Whole claim: {reason}", amount=billed, clause=clause)],
        reason=reason,
        clause=clause,
    )
