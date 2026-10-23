from pathlib import Path

import pytest

from backend.schemas import BillCategory, Case, DocumentType

FIXTURES = sorted(Path("fixtures").glob("case*.json"))


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text())


def test_three_fixtures_exist() -> None:
    assert [p.name for p in FIXTURES] == ["case1.json", "case2.json", "case3.json"]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path: Path) -> None:
    case = Case.model_validate_json(path.read_text())
    final_bill = next(d for d in case.documents if d.type == DocumentType.FINAL_BILL)
    assert sum(line.amount for line in case.bill_lines) == final_bill.fields["total_billed"]
    room = next(line for line in case.bill_lines if line.category == BillCategory.ROOM)
    assert room.quantity == (case.admission.discharge_date - case.admission.admit_date).days


def test_case2_is_ramesh() -> None:
    case = load("case2.json")
    room = next(line for line in case.bill_lines if line.category == BillCategory.ROOM)
    assert case.policy.sum_insured == 300_000
    assert room.amount // room.quantity == 5_000
    cap = next(r for r in case.policy.rules if r.type == "room_rent_cap")
    assert cap.value.percent_of_sum_insured == 1.0 and cap.proportionate
    assert sum(line.amount for line in case.bill_lines) == 240_000
    assert DocumentType.XRAY_REPORT not in {d.type for d in case.documents}


def test_case3_missing_admission_reason() -> None:
    case = load("case3.json")
    summary = next(d for d in case.documents if d.type == DocumentType.DISCHARGE_SUMMARY)
    assert case.admission.tpa_id == "TPA-B"
    assert summary.fields["reason_for_admission"] is None


def test_case1_is_clean() -> None:
    case = load("case1.json")
    summary = next(d for d in case.documents if d.type == DocumentType.DISCHARGE_SUMMARY)
    room = next(line for line in case.bill_lines if line.category == BillCategory.ROOM)
    assert summary.fields["reason_for_admission"]
    assert room.amount // room.quantity <= case.policy.sum_insured // 100
    assert BillCategory.CONSUMABLES not in {line.category for line in case.bill_lines}
