"""Historical claim outcomes: rules engine + the TPA's hidden profile + a little noise (design section 8).

These are the labels the denial-risk model trains on. Every rupee comes from the rules engine.
"""

import random
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.rules.engine import assess
from backend.schemas import BillCategory, Case, ClaimState, Rule, RuleType
from data.generator.admissions import ADMISSION_REASON, Generated, Problem, documentation_gaps
from data.generator.procedures import BY_SLUG
from data.generator.tpa_profiles import TPA_PROFILES, TpaProfile

NOISE_RATE = 0.03  # share of eligible claims whose outcome flips for no reason in the record

_STAY_CHARGES = {BillCategory.ROOM, BillCategory.ICU, BillCategory.NURSING}

Outcome = Literal[ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED, ClaimState.DENIED]


class ClaimLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admission_id: str
    tpa_id: str
    planted_problem: Problem
    problem_detail: str | None
    outcome: Outcome
    billed: int
    approved_amount: int
    reasons: list[str]
    noise: bool = False


def adjudicate(generated: Generated, rng: random.Random) -> ClaimLabel:
    case = generated.case
    procedure = BY_SLUG[case.admission.procedure]
    profile = TPA_PROFILES[case.admission.tpa_id]
    gaps = documentation_gaps(case)
    tpa_case = _with_rules(case, _tpa_rules(case.policy.rules, profile))
    assessment = assess(tpa_case)
    rule_types = {rule.rule_id: rule.type for rule in case.policy.rules}
    cuts = [d for d in assessment.deductions if rule_types.get(d.rule_id) != RuleType.CO_PAY]
    paperwork_gaps = [gap for gap in gaps if gap not in ("xray_report", ADMISSION_REASON)]

    outcome: Outcome
    on_paperwork = True  # denied for paperwork, not policy: a lenient reviewer might let it through
    if case.admission.icd10 != procedure.icd10:
        outcome, approved = ClaimState.DENIED, 0
        reasons = [f"Diagnosis code {case.admission.icd10} does not match {procedure.diagnosis.lower()}"]
    elif paperwork_gaps:
        outcome, approved = ClaimState.DENIED, 0
        reasons = [f"{gap.replace('_', ' ').capitalize()} not submitted" for gap in paperwork_gaps]
    elif "xray_report" in gaps and profile.requires_xray_for_ortho:
        outcome, approved, reasons = ClaimState.DENIED, 0, ["Pre-op X-ray report not submitted"]
    else:
        on_paperwork = False
        if assessment.payable == 0:
            outcome, approved = ClaimState.DENIED, 0
            reasons = [f"{d.reason} (clause {d.clause_id})" for d in cuts[:1]]
        elif ADMISSION_REASON in gaps and profile.denies_without_admission_reason:
            treatment_only = tpa_case.model_copy(
                update={"bill_lines": [l for l in case.bill_lines if l.category not in _STAY_CHARGES]}
            )
            outcome, approved = ClaimState.PARTIALLY_APPROVED, assess(treatment_only).payable
            reasons = ["Discharge summary does not state why admission was needed; stay charges not paid"]
        elif cuts:
            outcome, approved = ClaimState.PARTIALLY_APPROVED, assessment.payable
            reasons = sorted({f"{d.reason} (clause {d.clause_id})" for d in cuts})
        else:
            outcome, approved, reasons = ClaimState.APPROVED, assessment.payable, []

    noise = False
    if outcome == ClaimState.APPROVED and rng.random() < NOISE_RATE:
        outcome, approved, reasons, noise = ClaimState.DENIED, 0, ["Rejected on review"], True
    elif outcome == ClaimState.DENIED and on_paperwork and rng.random() < NOISE_RATE:
        outcome, approved, reasons, noise = ClaimState.APPROVED, assessment.payable, [], True

    return ClaimLabel(
        admission_id=case.admission.admission_id,
        tpa_id=profile.tpa_id,
        planted_problem=generated.problem,
        problem_detail=generated.detail,
        outcome=outcome,
        billed=assessment.billed,
        approved_amount=approved,
        reasons=reasons,
        noise=noise,
    )


def _tpa_rules(rules: list[Rule], profile: TpaProfile) -> list[Rule]:
    """The policy rules as this TPA applies them."""
    if not profile.proportionate_includes_pharmacy:
        return rules
    return [
        rule.model_copy(update={
            "linked_charges": [*rule.linked_charges, BillCategory.PHARMACY],
            "excluded_from_proportionate": [
                c for c in rule.excluded_from_proportionate if c != BillCategory.PHARMACY
            ],
        })
        if rule.proportionate and BillCategory.PHARMACY not in rule.linked_charges
        else rule
        for rule in rules
    ]


def _with_rules(case: Case, rules: list[Rule]) -> Case:
    return case.model_copy(update={"policy": case.policy.model_copy(update={"rules": rules})})
