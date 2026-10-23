"""Ramesh's knee replacement, the worked example in design section 1. This must always pass."""

from pathlib import Path

from backend.rules.engine import assess, what_if_room
from backend.schemas import Case

RAMESH = Case.model_validate_json(Path("fixtures", "case2.json").read_text())


def test_ramesh_totals() -> None:
    result = assess(RAMESH)
    assert result.billed == 240_000
    assert result.payable == 185_800
    assert result.patient_share == 54_200


def test_ramesh_line_by_line_matches_worked_example() -> None:
    payable = {line.line_id: line.payable for line in assess(RAMESH).lines}
    assert payable == {
        "ADM-0002-L1": 12_000,  # room capped at ₹3,000 a day
        "ADM-0002-L2": 36_000,  # doctor fees cut to 60%
        "ADM-0002-L3": 4_800,  # nursing cut to 60%
        "ADM-0002-L4": 18_000,  # OT cut to 60%
        "ADM-0002-L5": 90_000,  # implant not linked to the room
        "ADM-0002-L6": 25_000,  # medicines not linked to the room
        "ADM-0002-L7": 0,  # consumables are non-payable
    }


def test_ramesh_deductions_cite_their_clauses() -> None:
    deductions = {d.line_id: (d.amount, d.rule_id, d.clause_id) for d in assess(RAMESH).deductions}
    assert deductions == {
        "ADM-0002-L7": (7_000, "POL-A-ANX2", "Annexure II"),
        "ADM-0002-L1": (8_000, "POL-A-4.2", "4.2"),
        "ADM-0002-L2": (24_000, "POL-A-4.2", "4.2"),
        "ADM-0002-L3": (3_200, "POL-A-4.2", "4.2"),
        "ADM-0002-L4": (12_000, "POL-A-4.2", "4.2"),
    }


def test_ramesh_in_a_3000_room_pays_only_consumables() -> None:
    result = what_if_room(RAMESH, 3_000)
    assert result.patient_share == 7_000
    assert [d.rule_id for d in result.deductions] == ["POL-A-ANX2"]


def test_the_dearer_room_cost_ramesh_47200() -> None:
    assert assess(RAMESH).patient_share - what_if_room(RAMESH, 3_000).patient_share == 47_200
