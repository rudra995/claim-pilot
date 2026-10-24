"""Reconciliation agent: checks what was paid against the bill, and finds what is worth chasing (design section 4).

Each deduction in the insurer's final decision is set against what the policy, priced by the rules
engine, cuts under the same clause. Up to the policy's amount a deduction is valid; beyond it, it is
recoverable. A payment short of the approved amount is recoverable too. The amount to chase is what
the policy pays minus what was paid, so the policy's own co-pay on a recovered amount is allowed for.
A gap at or above GAP_THRESHOLD goes to a person.
"""

from backend.agents.base import HUMAN, AgentResult
from backend.rules.engine import ClauseGap, Reconciliation, assess, reconcile_payment
from backend.schemas import AgentOutput, Approve, Case, Final, Finding, PartialApprove

AGENT = "reconciliation"
GAP_THRESHOLD = 1_000  # rupees: a gap this size or more is worth a person's time

GapReport = Reconciliation  # the engine does every sum; this agent explains them


def run(claim_id: str, case: Case, decision: Approve | PartialApprove | Final, requested: int, paid: int) -> AgentResult:
    assessment = assess(case)
    report = reconcile_payment(assessment, requested, ((d.clause, d.item, d.amount) for d in decision.deductions), decision.approved_amount, paid)
    clauses, short_paid, expected = report.clauses, report.short_paid, report.expected
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
