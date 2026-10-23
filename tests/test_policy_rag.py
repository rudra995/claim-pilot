"""Phase 4: clause splitting, policy search and rule extraction."""

import json
from pathlib import Path

import pytest

from backend.rag.clauses import Clause, drop_running_heads, split_clauses
from backend.rag.extract import extract_rules
from backend.rag.ingest import pieces
from backend.rag.review import verify
from backend.rag.search import ClauseHit
from backend.schemas import Rule, RuleType

POLICIES = sorted(Path("data/policies").glob("*.pdf"))
RULE_FILES = sorted(Path("data/rules").glob("*.json"))
ROOM_RENT_CLAUSES = {  # checked by hand against each PDF
    "godigit_arogya_sanjeevani": "C.1",
    "iffco_tokio_arogya_sanjeevani": "4.1",
    "new_india_mediclaim": "3.1",
    "sbi_general": "4.1",
}


def split(*lines: str) -> dict[str, Clause]:
    return {c.clause_id: c for c in split_clauses("test", [(1, line) for line in lines])}


def test_splits_on_dotted_clause_numbers() -> None:
    clauses = split("1. PREAMBLE", "Intro text.", "2. COVERAGE", "2.1 Room rent up to 1% a day.", "2.2 ICU up to 2%.")
    assert list(clauses) == ["1", "2", "2.1", "2.2"]
    assert clauses["2.1"].text == "2.1 Room rent up to 1% a day."


def test_list_items_stay_in_their_clause() -> None:
    clauses = split(
        "3. BENEFITS",
        "3.2 PROPORTIONATE DEDUCTION applies except on",
        "1. Cost of Pharmacy",
        "2. Cost of Implants",
        "3.3 AMBULANCE",
    )
    assert list(clauses) == ["3", "3.2", "3.3"]
    assert "Cost of Implants" in clauses["3.2"].text


def test_list_item_that_runs_on_out_of_order_is_not_a_clause() -> None:
    # "7." looks like the clause after 6, but 6.3 comes later, so it is item 7 of a list in 6.2.
    clauses = split("6 Waiting Period", "6.2 Specific waiting period", "7. Benign tumours", "6.3 First 30 days", "7 EXCLUSIONS")
    assert list(clauses) == ["6", "6.2", "6.3", "7"]
    assert "Benign tumours" in clauses["6.2"].text


def test_number_alone_on_its_line_joins_its_title() -> None:
    assert "4.1" in split("4. COVERAGE", "4.1.", "Hospitalization", "Room rent up to 2%.")


def test_lettered_sections() -> None:
    clauses = split("A. PREAMBLE", "B. DEFINITIONS", "1. Accident means", "2. Room Rent means", "C. BENEFITS", "1. Hospitalization")
    assert list(clauses) == ["A", "B", "B.1", "B.2", "C", "C.1"]
    assert clauses["B.2"].kind == "definition"
    assert clauses["C.1"].kind == "clause"


def test_annexure_is_one_clause() -> None:
    clauses = split("9. CLAIMS", "Annexure II: List of non-payable items", "1 BABY FOOD", "2 GLOVES", "Annexure-A respectively.")
    assert list(clauses) == ["9", "Annexure II"]
    assert clauses["Annexure II"].kind == "annexure"
    assert "GLOVES" in clauses["Annexure II"].text


def test_running_heads_dropped() -> None:
    lines = [(p, line) for p in range(1, 6) for line in (f"Page {p} of 5", "Policy Wording UIN X1", f"text {p}", "1.")]
    kept = [line for _, line in drop_running_heads(lines, 5)]
    assert "Policy Wording UIN X1" not in kept and "Page 1 of 5" not in kept
    assert "1." in kept  # bare list numbers are not headers


def test_list_items_are_indexed_separately() -> None:
    text = "Expenses for, i. Room Rent up to 2% of sum insured. ii. ICU up to 5%. iii. Surgeon fees."
    assert pieces(text) == ["Expenses for,", "i. Room Rent up to 2% of sum insured.", "ii. ICU up to 5%.", "iii. Surgeon fees."]


ROOM_CLAUSE = ClauseHit(
    "p", "4.1", "Hospitalization", 6,
    "4.1 Room Rent, Boarding, Nursing Expenses up to 2% of the sum insured subject to maximum of Rs.5,000/- per day."
    " Other expenses, except medicines, are paid in the same proportion.",
    0.3,
)
GOOD_ANSWER = json.dumps({"rules": [{
    "clause_id": "4.1",
    "value": {"percent_of_sum_insured": 2, "amount": 5000, "per": "day"},
    "proportionate": True,
    "linked_charges": ["doctor_fees", "nursing", "ot_charges"],
    "excluded_from_proportionate": ["pharmacy"],
}]})


def answers(*replies: str):
    queue = list(replies)
    return lambda prompt: queue.pop(0)


def test_extracts_rule_with_clause_wording() -> None:
    rules, problem = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(GOOD_ANSWER))
    assert problem is None
    [rule] = rules
    assert rule.rule_id == "p-4.1-room_rent_cap"
    assert rule.value.amount == 5000 and rule.value.percent_of_sum_insured == 2
    assert rule.clause_text == ROOM_CLAUSE.text  # the policy's words, not the LLM's
    assert rule.verified_by_human is False


@pytest.mark.parametrize(
    "bad",
    [
        GOOD_ANSWER.replace("5000", "6000"),  # a number that isn't in the clause
        GOOD_ANSWER.replace('"4.1"', '"9.9"'),  # a clause it wasn't shown
        GOOD_ANSWER.replace('"day"', '"week"'),  # not a valid rule
        "Sure! Here are the rules.",  # not JSON
    ],
)
def test_bad_answer_is_retried(bad: str) -> None:
    rules, problem = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(bad, GOOD_ANSWER))
    assert problem is None and len(rules) == 1


WAITING_CLAUSE = ClauseHit(
    "p", "4.2", "Waiting period", 8,
    "4.2 (i) 90 Days Waiting Period 1. Diabetes (ii) 24 Months waiting period 1. Hernia of all types 2. Cataract",
    0.3,
)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ({"procedures": ["diabetes"], "months": 90}, "90 months"),  # 90 days read as months
        ({"procedures": ["hernia_repair", "cabg"], "months": 24}, "cabg"),  # a name the clause never uses
    ],
)
def test_rule_must_match_its_clause(value: dict, reason: str) -> None:
    bad = json.dumps({"rules": [{"clause_id": "4.2", "value": value}]})
    rules, problem = extract_rules("p", RuleType.WAITING_PERIOD, [WAITING_CLAUSE], answers(bad, bad, bad))
    assert rules == [] and reason in (problem or "")


def test_waiting_period_mapped_to_our_procedure_names() -> None:
    good = json.dumps({"rules": [{"clause_id": "4.2", "value": {"procedures": ["hernia_repair", "cataract_surgery"], "months": 24}}]})
    [rule], problem = extract_rules("p", RuleType.WAITING_PERIOD, [WAITING_CLAUSE], answers(good))
    assert problem is None and rule.value.procedures == ["hernia_repair", "cataract_surgery"]


def test_layout_slips_are_tidied() -> None:
    # proportionate written inside value, "per day" for "day", and the child clause 4.1.1 cited
    sloppy = json.loads(GOOD_ANSWER)
    rule = sloppy["rules"][0]
    rule["value"]["proportionate"] = rule.pop("proportionate")
    rule["value"]["per"] = "per day"
    rule["clause_id"] = "4.1.1"
    [parsed], problem = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(json.dumps(sloppy)))
    assert problem is None and parsed.proportionate and parsed.clause_id == "4.1"


def test_only_one_room_cap() -> None:
    two = json.loads(GOOD_ANSWER)
    two["rules"].append(dict(two["rules"][0], value={"percent_of_sum_insured": 2, "per": "day"}))
    rules, problem = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(*[json.dumps(two)] * 3))
    assert rules == [] and "exactly one" in (problem or "")


def test_waiting_period_clause_is_not_an_exclusion() -> None:
    bad = json.dumps({"rules": [{"clause_id": "4.2", "value": {"procedures": ["hernia_repair"]}}]})
    rules, problem = extract_rules("p", RuleType.EXCLUSION, [WAITING_CLAUSE], answers(bad, bad, bad))
    assert rules == [] and "not a permanent exclusion" in (problem or "")


def test_gives_up_with_reason_after_repeated_bad_answers() -> None:
    bad = GOOD_ANSWER.replace("5000", "6000")
    rules, problem = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(bad, bad, bad))
    assert rules == [] and "6000" in (problem or "")


def test_no_rule_of_this_type_is_fine() -> None:
    assert extract_rules("p", RuleType.CO_PAY, [ROOM_CLAUSE], answers('{"rules": []}')) == ([], None)


def test_verify_marks_only_named_rules(tmp_path: Path) -> None:
    rules = extract_rules("p", RuleType.ROOM_RENT_CAP, [ROOM_CLAUSE], answers(GOOD_ANSWER))[0]
    (tmp_path / "p.json").write_text(json.dumps({"policy_id": "p", "rules": [r.model_dump(mode="json") for r in rules], "skipped": []}))
    assert verify("p", ["nope"], tmp_path) == ["nope"]
    assert verify("p", ["p-4.1-room_rent_cap"], tmp_path) == []
    assert json.loads((tmp_path / "p.json").read_text())["rules"][0]["verified_by_human"] is True


# Checks on the real policies; they run once the PDFs are ingested and rules extracted on this machine.

@pytest.mark.skipif(not Path("data/chroma").exists(), reason="run python -m backend.rag.ingest first")
@pytest.mark.parametrize("policy_id", sorted(ROOM_RENT_CLAUSES))
def test_room_rent_search_finds_the_room_rent_clause(policy_id: str) -> None:
    from backend.rag.search import search

    hits = search("room rent limit", policy_id=policy_id, k=3)
    assert ROOM_RENT_CLAUSES[policy_id] in [hit.clause_id for hit in hits]


@pytest.mark.skipif(not RULE_FILES, reason="run python -m backend.rag.extract first")
@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.stem)
def test_every_extracted_rule_validates(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rules"], "no rules extracted"
    for rule in data["rules"]:
        Rule.model_validate(rule)
