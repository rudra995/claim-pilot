import json
from pathlib import Path
from typing import Any

import pytest

from backend.rules.engine import SUM_INSURED_RULE_ID, Assessment, assess
from backend.schemas import Case

FIXTURES = sorted(Path("fixtures").glob("case*.json"))


def load(name: str) -> dict[str, Any]:
    return json.loads(Path("fixtures", name).read_text())


def run(data: dict[str, Any]) -> Assessment:
    return assess(Case.model_validate(data))


def rule(rule_id: str, type: str, value: dict[str, Any], clause_id: str = "9.1") -> dict[str, Any]:
    return {"rule_id": rule_id, "type": type, "value": value, "clause_id": clause_id, "clause_text": "Test clause."}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_deductions_account_for_every_rupee(path: Path) -> None:
    result = run(json.loads(path.read_text()))
    assert result.patient_share == result.billed - result.payable
    assert sum(d.amount for d in result.deductions) == result.patient_share
    assert all(d.rule_id and d.clause_id for d in result.deductions)


def test_clean_case_is_paid_in_full() -> None:
    result = run(load("case1.json"))  # ₹4,000 room under a ₹5,000 cap
    assert result.payable == result.billed == 98_000
    assert result.deductions == []


def test_non_payable_matched_by_item_name() -> None:
    data = load("case1.json")
    data["bill_lines"].append(
        {"line_id": "ADM-0001-L7", "category": "other", "description": "Admission kit", "amount": 1_500}
    )
    result = run(data)
    assert result.payable == 98_000
    [deduction] = result.deductions
    assert (deduction.line_id, deduction.amount, deduction.clause_id) == ("ADM-0001-L7", 1_500, "Annexure I")


def test_sub_limit_caps_the_procedure_total() -> None:
    data = load("case1.json")
    data["policy"]["rules"].append(
        rule("POL-C-7.2", "sub_limit", {"procedure": "laparoscopic_appendectomy", "amount": 60_000}, "7.2")
    )
    result = run(data)
    assert result.payable == 60_000
    [deduction] = result.deductions
    assert (deduction.amount, deduction.clause_id, deduction.line_id) == (38_000, "7.2", None)


def test_sub_limit_for_another_procedure_does_not_apply() -> None:
    data = load("case1.json")
    data["policy"]["rules"].append(rule("POL-C-7.3", "sub_limit", {"procedure": "cataract", "amount": 40_000}))
    assert run(data).payable == 98_000


def test_co_pay_for_senior_patient() -> None:
    result = run(load("case3.json"))  # 64 years old, 10% co-pay at 60+
    assert result.payable == 38_250
    [deduction] = result.deductions
    assert (deduction.amount, deduction.rule_id, deduction.clause_id) == (4_250, "POL-B-6.1", "6.1")


def test_co_pay_skipped_below_min_age() -> None:
    data = load("case3.json")
    data["patient"]["age"] = 55
    assert run(data).payable == 42_500


def test_co_pay_applies_after_room_cap() -> None:
    data = load("case2.json")
    data["policy"]["rules"].append(rule("POL-A-6.1", "co_pay", {"percent": 10}, "6.1"))
    result = run(data)
    assert result.payable == 185_800 - 18_580
    assert result.deductions[-1].amount == 18_580


def test_waiting_period_not_served_denies_whole_claim() -> None:
    data = load("case2.json")
    data["policy"]["start_date"] = "2025-04-01"  # 18 months before admission, 24 needed
    result = run(data)
    assert result.payable == 0
    assert result.patient_share == 240_000
    assert {d.clause_id for d in result.deductions} == {"5.3"}
    assert "18 months covered" in result.deductions[0].reason


@pytest.mark.parametrize(("start_date", "payable"), [("2024-10-05", 185_800), ("2024-10-06", 0)])
def test_waiting_period_boundary(start_date: str, payable: int) -> None:
    data = load("case2.json")  # admitted 2026-10-05
    data["policy"]["start_date"] = start_date
    assert run(data).payable == payable


def test_exclusion_denies_whole_claim() -> None:
    data = load("case1.json")
    data["policy"]["rules"].append(
        rule("POL-C-8.1", "exclusion", {"procedures": ["laparoscopic_appendectomy"]}, "8.1")
    )
    result = run(data)
    assert result.payable == 0
    assert {d.clause_id for d in result.deductions} == {"8.1"}


def test_icu_cap_without_proportionate_cut() -> None:
    data = load("case1.json")
    data["bill_lines"].append(
        {"line_id": "ADM-0001-L7", "category": "icu", "description": "ICU, 2 days", "quantity": 2, "amount": 30_000}
    )
    data["policy"]["rules"].append(rule("POL-C-3.2", "icu_cap", {"amount": 10_000}, "3.2"))
    result = run(data)
    assert result.payable == 98_000 + 20_000
    [deduction] = result.deductions
    assert (deduction.line_id, deduction.amount) == ("ADM-0001-L7", 10_000)


def test_sum_insured_ceiling_applies_last() -> None:
    data = load("case3.json")
    data["policy"]["sum_insured"] = 30_000
    data["policy"]["rules"] = [r for r in data["policy"]["rules"] if r["type"] != "room_rent_cap"]
    result = run(data)  # ₹42,500 bill, 10% co-pay → ₹38,250, then capped at ₹30,000
    assert result.payable == 30_000
    assert [(d.rule_id, d.amount) for d in result.deductions] == [("POL-B-6.1", 4_250), (SUM_INSURED_RULE_ID, 8_250)]
