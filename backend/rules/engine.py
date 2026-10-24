"""Policy rules engine (design section 6).

Plain Python applies a policy's rules to a bill, so every rupee of deduction is calculated,
never guessed, and cites the rule and clause it came from. Amounts are whole rupees.

Order: whole-claim rules (exclusion, waiting period), then non-payables, room and ICU caps with
proportionate cuts, sub-limits, co-pay, and last the sum-insured ceiling.
"""

import math
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import BillCategory, BillLine, Case, Rule, RuleType
from backend.schemas.rules import DailyCapValue, SubLimitValue

# The sum insured comes from the policy schedule, not an extracted rule.
SUM_INSURED_RULE_ID = "SUM_INSURED"
SUM_INSURED_CLAUSE_ID = "Policy schedule"

_CAP_TARGETS = {
    RuleType.ROOM_RENT_CAP: (BillCategory.ROOM, "Room rent"),
    RuleType.ICU_CAP: (BillCategory.ICU, "ICU charges"),
}


class RuleDeduction(BaseModel):
    """One amount the insurer won't pay, with the rule and clause behind it."""

    model_config = ConfigDict(extra="forbid")

    item: str
    amount: int = Field(gt=0)
    rule_id: str
    clause_id: str
    reason: str
    line_id: str | None = None  # None for claim-level cuts: sub-limit, co-pay, sum insured


class LineAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    category: BillCategory
    description: str
    billed: int
    payable: int  # before claim-level cuts


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    billed: int
    payable: int  # what the insurer pays
    patient_share: int  # billed minus payable
    deductions: list[RuleDeduction]
    lines: list[LineAssessment]


def assess(case: Case) -> Assessment:
    """Apply the case's policy rules to its bill."""
    bill = _Bill(case.bill_lines)
    rules = case.policy.rules

    whole_claim = _whole_claim_rule(case)
    if whole_claim is not None:
        rule, reason = whole_claim
        for line in case.bill_lines:
            bill.cut_line(line, 0, rule, reason)
        return bill.result()

    for rule in _of_type(rules, RuleType.NON_PAYABLE):
        for line in case.bill_lines:
            if _is_non_payable(rule, line):
                bill.cut_line(line, 0, rule, "Non-payable item")

    for rule in _of_type(rules, RuleType.ROOM_RENT_CAP, RuleType.ICU_CAP):
        _apply_daily_cap(bill, rule, case)

    procedure = case.admission.procedure
    for rule in _of_type(rules, RuleType.SUB_LIMIT):
        limit = _round(_cap(rule.value, case.policy.sum_insured))
        if rule.value.procedure == procedure and bill.total > limit:
            bill.cut_claim(
                bill.total - limit, rule, f"{procedure} above sub-limit", f"Capped at ₹{limit:,}"
            )

    for rule in _of_type(rules, RuleType.CO_PAY):
        min_age = rule.value.min_age
        if min_age is None or case.patient.age >= min_age:
            amount = _round(bill.total * _exact(rule.value.percent) / 100)
            reason = f"{rule.value.percent:g}% co-pay" + (f" for patients aged {min_age}+" if min_age else "")
            bill.cut_claim(amount, rule, "Co-pay", reason)

    sum_insured = case.policy.sum_insured
    if bill.total > sum_insured:
        bill.cut_claim(
            bill.total - sum_insured,
            None,
            "Above sum insured",
            f"Claim capped at the sum insured of ₹{sum_insured:,}",
        )

    return bill.result()


def what_if_room(case: Case, rate: int) -> Assessment:
    """Assess the same stay as if every room line had been charged `rate` a day."""
    lines = [
        line.model_copy(update={"amount": rate * line.quantity})
        if line.category == BillCategory.ROOM
        else line
        for line in case.bill_lines
    ]
    return assess(case.model_copy(update={"bill_lines": lines}))


# ---- Money helpers ---------------------------------------------------------------------------
# Every rupee sum or difference outside the assessment itself goes through these, so agents, the API
# and the benchmark never do their own arithmetic on amounts (refinement of phase 1).


def total(amounts: Iterable[int]) -> int:
    """The sum of whole-rupee amounts."""
    return sum(int(amount) for amount in amounts)


def billed(lines: Iterable[BillLine]) -> int:
    return total(line.amount for line in lines)


def by_clause(cuts: Iterable[tuple[str, int]]) -> dict[str, int]:
    """(clause, amount) pairs added up per clause, in first-seen order."""
    totals: dict[str, int] = {}
    for clause, amount in cuts:
        totals[clause] = totals.get(clause, 0) + int(amount)
    return totals


def excess(amount: int, allowed: int) -> int:
    """How far `amount` is above `allowed` (0 if it isn't)."""
    return max(0, int(amount) - int(allowed))


def payable_up_to(assessment: "Assessment", requested: int) -> int:
    """What the insurer should pay: the policy's payable, never more than was asked for."""
    return min(assessment.payable, int(requested))


def share(part: int, whole: int) -> float | None:
    """part / whole as a fraction, for rates; None when there is nothing to divide by."""
    return round(part / whole, 4) if whole else None


class _Bill:
    """Running payable amounts while rules are applied, plus every deduction made."""

    def __init__(self, lines: list[BillLine]) -> None:
        self.lines = lines
        self.payable = {line.line_id: line.amount for line in lines}
        self.claim_cuts = 0
        self.deductions: list[RuleDeduction] = []

    @property
    def total(self) -> int:
        return sum(self.payable.values()) - self.claim_cuts

    def cut_line(self, line: BillLine, new_payable: int, rule: Rule, reason: str) -> None:
        amount = self.payable[line.line_id] - new_payable
        if amount <= 0:
            return
        self.payable[line.line_id] = new_payable
        self.deductions.append(
            RuleDeduction(
                item=line.description,
                amount=amount,
                rule_id=rule.rule_id,
                clause_id=rule.clause_id,
                reason=reason,
                line_id=line.line_id,
            )
        )

    def cut_claim(self, amount: int, rule: Rule | None, item: str, reason: str) -> None:
        if amount <= 0:
            return
        self.claim_cuts += amount
        self.deductions.append(
            RuleDeduction(
                item=item,
                amount=amount,
                rule_id=rule.rule_id if rule else SUM_INSURED_RULE_ID,
                clause_id=rule.clause_id if rule else SUM_INSURED_CLAUSE_ID,
                reason=reason,
            )
        )

    def result(self) -> Assessment:
        billed = sum(line.amount for line in self.lines)
        payable = self.total
        return Assessment(
            billed=billed,
            payable=payable,
            patient_share=billed - payable,
            deductions=self.deductions,
            lines=[
                LineAssessment(
                    line_id=line.line_id,
                    category=line.category,
                    description=line.description,
                    billed=line.amount,
                    payable=self.payable[line.line_id],
                )
                for line in self.lines
            ],
        )


def _whole_claim_rule(case: Case) -> tuple[Rule, str] | None:
    """An exclusion or unserved waiting period that rules out the whole claim, if any."""
    procedure = case.admission.procedure
    for rule in _of_type(case.policy.rules, RuleType.EXCLUSION):
        if procedure in rule.value.procedures:
            return rule, f"{procedure} is excluded by the policy"
    for rule in _of_type(case.policy.rules, RuleType.WAITING_PERIOD):
        covered = _months_between(case.policy.start_date, case.admission.admit_date)
        if procedure in rule.value.procedures and covered < rule.value.months:
            return rule, (
                f"{rule.value.months}-month waiting period not served"
                f" ({covered} months covered)"
            )
    return None


def _is_non_payable(rule: Rule, line: BillLine) -> bool:
    description = line.description.lower()
    return line.category in rule.value.categories or any(
        item.lower() in description for item in rule.value.items
    )


def _apply_daily_cap(bill: _Bill, rule: Rule, case: Case) -> None:
    """Cap room (or ICU) lines per day; if proportionate, cut linked charges by the same ratio."""
    daily_cap = _cap(rule.value, case.policy.sum_insured)
    target, label = _CAP_TARGETS[rule.type]
    capped_lines = [
        line for line in case.bill_lines if line.category == target and bill.payable[line.line_id]
    ]
    actual = sum(bill.payable[line.line_id] for line in capped_lines)
    eligible = 0
    for line in capped_lines:
        line_eligible = min(bill.payable[line.line_id], _round(daily_cap * line.quantity))
        eligible += line_eligible
        bill.cut_line(line, line_eligible, rule, f"{label} capped at ₹{_round(daily_cap):,} a day")

    if not rule.proportionate or eligible >= actual:
        return
    ratio = Fraction(eligible, actual)
    for line in case.bill_lines:
        if line.category in rule.linked_charges:
            bill.cut_line(
                line,
                _round(bill.payable[line.line_id] * ratio),
                rule,
                f"Proportionate deduction: paid at {float(ratio) * 100:.4g}% of billed,"
                f" the ratio of eligible to actual {label.lower()}",
            )


def _cap(value: DailyCapValue | SubLimitValue, sum_insured: int) -> Fraction:
    """The cap in rupees: a share of sum insured, a fixed amount, or the lower of the two."""
    caps = []
    if value.percent_of_sum_insured is not None:
        caps.append(sum_insured * _exact(value.percent_of_sum_insured) / 100)
    if value.amount is not None:
        caps.append(Fraction(value.amount))
    return min(caps)


def _of_type(rules: list[Rule], *types: RuleType) -> list[Rule]:
    return [rule for rule in rules if rule.type in types]


def _months_between(start: date, end: date) -> int:
    """Whole months from start to end (a month counts once its day of the month is reached)."""
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if end.day < start.day else months


def _exact(number: float) -> Fraction:
    """Turn a percent like 1.0 or 12.5 into an exact fraction, so no float error leaks into rupees."""
    return Fraction(Decimal(str(number)))


def _round(amount: Fraction) -> int:
    """Round to the nearest rupee, halves up."""
    return math.floor(amount + Fraction(1, 2))
