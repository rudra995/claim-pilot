import pytest
from pydantic import ValidationError

from backend.schemas import (
    AgentOutput,
    ClaimState,
    Finding,
    MessageType,
    PartialApprove,
    Rule,
    Sender,
    claim_message_adapter,
)
from backend.schemas.rules import DailyCapValue

RAMESH_RULE = {
    "rule_id": "POL-A-4.2",
    "type": "room_rent_cap",
    "value": {"percent_of_sum_insured": 1.0, "per": "day"},
    "clause_id": "4.2",
    "clause_text": "Room rent up to 1% of sum insured per day.",
    "proportionate": True,
    "linked_charges": ["doctor_fees", "nursing", "ot_charges"],
    "excluded_from_proportionate": ["implants", "pharmacy"],
    "verified_by_human": True,
}


def test_claim_states_match_design() -> None:
    assert len(ClaimState) == 14
    assert ClaimState("AWAITING_HUMAN") is ClaimState.AWAITING_HUMAN


def test_agent_output_from_design_example() -> None:
    out = AgentOutput.model_validate(
        {
            "agent": "denial_risk",
            "claim_id": "CLM-0042",
            "decision": "fix_before_submit",
            "confidence": 0.81,
            "findings": [
                {
                    "issue": "Room rent above policy cap",
                    "evidence": "Bill line 3: ₹5,000 per day",
                    "source": "Policy clause 4.2",
                }
            ],
            "next": "human_approval",
        }
    )
    assert out.findings[0].source == "Policy clause 4.2"


@pytest.mark.parametrize("source", [None, "", "   "])
def test_finding_without_source_is_invalid(source: str | None) -> None:
    data = {"issue": "x", "evidence": "y"}
    if source is not None:
        data["source"] = source
    with pytest.raises(ValidationError):
        Finding.model_validate(data)


def test_confidence_must_be_between_0_and_1() -> None:
    with pytest.raises(ValidationError):
        AgentOutput(agent="a", claim_id="c", decision="d", confidence=1.2, next="n")


def test_rule_from_design_example() -> None:
    rule = Rule.model_validate(RAMESH_RULE)
    assert isinstance(rule.value, DailyCapValue)
    assert Rule.model_validate_json(rule.model_dump_json()) == rule


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "sub_limit", "value": {"procedure": "cataract", "amount": 40000}},
        {"type": "waiting_period", "value": {"procedures": ["total_knee_replacement"], "months": 24}},
        {"type": "co_pay", "value": {"percent": 10, "min_age": 60}},
        {"type": "non_payable", "value": {"items": ["gloves"]}},
        {"type": "exclusion", "value": {"procedures": ["cosmetic_surgery"]}},
        {"type": "icu_cap", "value": {"percent_of_sum_insured": 2.0}},
    ],
)
def test_every_rule_type_validates(rule: dict) -> None:
    Rule.model_validate({"rule_id": "R", "clause_id": "1", "clause_text": "t", **rule})


@pytest.mark.parametrize(
    "change",
    [
        {"value": {"percent_of_sum_insured": 1.0, "amount": 3000}},  # both caps
        {"value": {"percent": 10}},  # value shape of another type
        {"type": "co_pay", "value": {"percent": 10}},  # proportionate on a non-cap rule
        {"linked_charges": []},  # proportionate with nothing linked
        {"excluded_from_proportionate": ["nursing"]},  # linked and excluded
        {"type": "made_up"},
        {"clause_id": ""},
    ],
)
def test_bad_rules_are_rejected(change: dict) -> None:
    with pytest.raises(ValidationError):
        Rule.model_validate({**RAMESH_RULE, **change})


def test_partial_approve_from_design_example() -> None:
    msg = claim_message_adapter.validate_python(
        {
            "type": "PARTIAL_APPROVE",
            "claim_id": "CLM-0042",
            "stage": "discharge",
            "round": 1,
            "approved_amount": 185800,
            "deductions": [
                {"item": "Consumables", "amount": 7000, "clause": "Annexure II, non-payable items"},
                {"item": "Room rent above cap", "amount": 8000, "clause": "4.2 room rent limit"},
                {"item": "Linked charges", "amount": 39200, "clause": "4.2 proportionate deduction"},
            ],
        }
    )
    assert isinstance(msg, PartialApprove)
    assert msg.sender is Sender.INSURER
    assert sum(d.amount for d in msg.deductions) == 240000 - 185800


def test_all_message_types_covered() -> None:
    assert {t.value for t in MessageType} == {
        "SUBMIT", "QUERY", "RESPONSE", "APPROVE", "PARTIAL_APPROVE", "DENY", "APPEAL", "FINAL",
    }


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "DENY", "claim_id": "C", "stage": "preauth", "round": 4, "reason": "r", "clause": "1"},
        {"type": "DENY", "claim_id": "C", "stage": "preauth", "round": 1, "reason": "r"},
        {"type": "APPEAL", "claim_id": "C", "stage": "preauth", "round": 2, "argument": "a", "cited_clauses": []},
        {"type": "PARTIAL_APPROVE", "claim_id": "C", "stage": "preauth", "round": 1, "approved_amount": 1, "deductions": []},
        {"type": "UNKNOWN", "claim_id": "C", "stage": "preauth", "round": 1},
    ],
)
def test_bad_messages_are_rejected(msg: dict) -> None:
    with pytest.raises(ValidationError):
        claim_message_adapter.validate_python(msg)
