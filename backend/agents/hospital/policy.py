"""Policy agent: what the patient's policy allows for this stay, each point tied to its clause.

All amounts come from the rules engine run on the hospital's itemised bill; this agent only
reports them. A rule no person has checked yet counts as an ambiguous clause and goes to a person.
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.rules.engine import Assessment, assess
from backend.schemas import AgentOutput, Case, Finding, RuleType

AGENT = "policy"

RULE_NAMES = {
    RuleType.ROOM_RENT_CAP: "Room rent cap",
    RuleType.ICU_CAP: "ICU cap",
    RuleType.SUB_LIMIT: "Procedure sub-limit",
    RuleType.CO_PAY: "Co-pay",
    RuleType.WAITING_PERIOD: "Waiting period",
    RuleType.EXCLUSION: "Exclusion",
    RuleType.NON_PAYABLE: "Non-payable items",
}
_WHOLE_CLAIM = {RuleType.EXCLUSION, RuleType.WAITING_PERIOD}


class PolicyReview(BaseModel):
    covered: bool
    assessment: Assessment
    unverified_rules: list[str]


def run(claim_id: str, case: Case) -> AgentResult:
    assessment = assess(case)
    rules = {rule.rule_id: rule for rule in case.policy.rules}

    findings = [
        Finding(issue=RULE_NAMES[rule.type], evidence=rule.clause_text, source=f"Policy clause {rule.clause_id}")
        for rule in case.policy.rules
    ]
    findings += [
        Finding(
            issue=d.reason,
            evidence=f"{d.item}: ₹{d.amount:,} not payable" + (f" (bill line {d.line_id})" if d.line_id else ""),
            source=f"Policy clause {d.clause_id}",
        )
        for d in assessment.deductions
    ]
    findings.append(
        Finding(
            issue="Expected insurer share",
            evidence=f"₹{assessment.payable:,} of ₹{assessment.billed:,} billed; patient pays ₹{assessment.patient_share:,}",
            source="Rules engine",
        )
    )

    unverified = [rule.rule_id for rule in case.policy.rules if not rule.verified_by_human]
    for rule_id in unverified:
        findings.append(
            Finding(
                issue="Clause not yet checked by a person",
                evidence=f"Rule {rule_id} was extracted automatically",
                source=f"Policy clause {rules[rule_id].clause_id}",
            )
        )

    covered = not any(rules[d.rule_id].type in _WHOLE_CLAIM for d in assessment.deductions if d.rule_id in rules)
    needs_person = bool(unverified) or not covered
    if not covered:
        decision = "not_covered"
    elif assessment.deductions:
        decision = "covered_with_deductions"
    else:
        decision = "covered"
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision=decision,
            confidence=0.6 if unverified else 1.0,
            findings=findings,
            next=HUMAN if needs_person else "preauth",
        ),
        data=PolicyReview(covered=covered, assessment=assessment, unverified_rules=unverified),
    )
