"""Reconciliation agent: checks what was paid against the bill, and finds what is worth chasing (design section 4).

Each deduction in the insurer's final decision is set against what the policy, priced by the rules
engine, cuts under the same clause. Up to the policy's amount a deduction is valid; beyond it, it is
recoverable. A payment short of the approved amount is recoverable too. The amount to chase is what
the policy pays minus what was paid, so the policy's own co-pay on a recovered amount is allowed for.
A gap at or above GAP_THRESHOLD goes to a person.
"""

from collections import defaultdict

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.rules.engine import assess
from backend.schemas import AgentOutput, Approve, Case, Final, Finding, PartialApprove

AGENT = "reconciliation"
GAP_THRESHOLD = 1_000  # rupees: a gap this size or more is worth a person's time


class ClauseGap(BaseModel):
    clause: str
    items: list[str]
    insurer_cut: int
    policy_cut: int  # what the policy cuts under this clause
    valid: int  # the part of the insurer's cut the policy supports
    recoverable: int  # the part it doesn't


class GapReport(BaseModel):
    billed: int
    expected: int  # what the policy pays, up to the amount requested
    approved: int
    paid: int
    clauses: list[ClauseGap]
    valid_total: int
    recoverable_total: int  # recoverable deductions plus any payment short of the approval
    short_paid: int
    to_chase: int  # expected minus paid


def run(claim_id: str, case: Case, decision: Approve | PartialApprove | Final, requested: int, paid: int) -> AgentResult:
    assessment = assess(case)
    expected = min(assessment.payable, requested)
    policy_by_clause: dict[str, int] = defaultdict(int)
    for d in assessment.deductions:
        policy_by_clause[d.clause_id] += d.amount

    insurer: dict[str, list] = defaultdict(list)
    for d in decision.deductions:
        insurer[d.clause].append(d)
    clauses = []
    for clause, cuts in insurer.items():
        cut = sum(d.amount for d in cuts)
        valid = min(cut, policy_by_clause.get(clause, 0))
        clauses.append(ClauseGap(
            clause=clause, items=[d.item for d in cuts], insurer_cut=cut,
            policy_cut=policy_by_clause.get(clause, 0), valid=valid, recoverable=cut - valid,
        ))
    short_paid = max(0, decision.approved_amount - paid)
    report = GapReport(
        billed=assessment.billed,
        expected=expected,
        approved=decision.approved_amount,
        paid=paid,
        clauses=clauses,
        valid_total=sum(c.valid for c in clauses),
        recoverable_total=sum(c.recoverable for c in clauses) + short_paid,
        short_paid=short_paid,
        to_chase=max(0, expected - paid),
    )

    findings = [
        Finding(
            issue="Payment against the policy",
            evidence=f"Paid ₹{paid:,} of ₹{assessment.billed:,} billed; the policy pays ₹{expected:,}",
            source="Payment advice; rules engine",
        )
    ]
    for c in clauses:
        verdict = "Recoverable" if c.recoverable else "Valid"
        findings.append(Finding(
            issue=f"{verdict} deduction",
            evidence=f"₹{c.insurer_cut:,} cut ({'; '.join(c.items)}); the policy supports ₹{c.policy_cut:,} under this clause",
            source=f"Insurer decision, round {decision.round}: clause {c.clause}",
        ))
    if short_paid:
        findings.append(Finding(issue="Recoverable: paid short", evidence=f"₹{short_paid:,} less than the ₹{decision.approved_amount:,} approved", source="Payment advice"))
    findings.append(Finding(issue="Amount to chase", evidence=f"₹{report.to_chase:,}", source="Rules engine; payment advice"))

    big_gap = report.to_chase >= GAP_THRESHOLD
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="recover_gap" if report.to_chase else "settled_in_full",
            confidence=1.0,
            findings=findings,
            next=HUMAN if big_gap else "close",
        ),
        data=report,
    )
