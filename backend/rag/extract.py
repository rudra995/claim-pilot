"""Turn policy clauses into rule JSON with the LLM, once per policy (design section 6).

    python -m backend.rag.extract [policy_id ...]

For each rule type, search finds the policy's closest clauses and the LLM fills in the rule's
values. A rule is kept only if it validates against `Rule`, cites one of the clauses it was shown,
every number in it is printed in that clause (a waiting period as "N months"), and every procedure
it names shares a word with that clause. The clause text stored with the rule is the
policy's own wording, never the LLM's. Every rule starts `verified_by_human: false`; a person
checks it with `python -m backend.rag.review`.
"""

import json
import re
import sys
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend import llm
from backend.rag.ingest import pieces
from backend.rag.search import ClauseHit, search
from backend.schemas import BillCategory, Rule, RuleType
from backend.schemas.rules import VALUE_MODELS
from data.generator.procedures import BY_SLUG

RULES_DIR = Path("data/rules")
MAX_ATTEMPTS = 3
CLAUSES_PER_RULE = 3
CLAUSE_CHARS = 2_000  # keep prompts inside a small local model's context window
# Words too general to show that a procedure name comes from the clause.
_GENERIC_WORDS = {"surgery", "surgical", "treatment", "management", "disease", "diseases", "disorders", "related"}

Complete = Callable[[str], str]

QUERIES = {
    RuleType.ROOM_RENT_CAP: "room rent boarding nursing expenses limit per day",
    RuleType.ICU_CAP: "ICU ICCU intensive care charges limit per day",
    RuleType.SUB_LIMIT: "cataract treatment limit per eye sub-limit",
    RuleType.WAITING_PERIOD: "specific waiting period months for listed illnesses and surgeries",
    RuleType.CO_PAY: "co-payment percentage of admissible claim amount",
    RuleType.NON_PAYABLE: "list of items for which coverage is not available non-payable",
    RuleType.EXCLUSION: "permanent exclusions expenses not covered cosmetic surgery",
}

_VALUE_SHAPES = {
    RuleType.ROOM_RENT_CAP: '{"percent_of_sum_insured": <number or null>, "amount": <rupees or null>, "per": "day"}',
    RuleType.ICU_CAP: '{"percent_of_sum_insured": <number or null>, "amount": <rupees or null>, "per": "day"}',
    RuleType.SUB_LIMIT: '{"procedure": "<procedure>", "percent_of_sum_insured": <number or null>, "amount": <rupees or null>}',
    RuleType.WAITING_PERIOD: '{"procedures": ["<procedure>", ...], "months": <number>}',
    RuleType.CO_PAY: '{"percent": <number>, "min_age": <age or null>}',
    RuleType.NON_PAYABLE: '{"categories": ["consumables"], "items": ["<item as written>", ...]}',
    RuleType.EXCLUSION: '{"procedures": ["<procedure>", ...]}',
}

SYSTEM = (
    "You turn insurance policy clauses into JSON rules. Copy numbers exactly as the clause states"
    " them; never calculate, round or invent a number. Answer with one JSON object and nothing else."
)


def extract_policy(
    policy_id: str,
    complete: Complete | None = None,
    find: Callable[[str, str], list[ClauseHit]] | None = None,
) -> dict[str, Any]:
    """All rules for one policy, plus the rule types that produced nothing and why."""
    complete = complete or _llm(policy_id)
    find = find or (lambda query, pid: search(query, policy_id=pid, k=CLAUSES_PER_RULE))
    rules: list[Rule] = []
    skipped: list[dict[str, str]] = []
    for rule_type, query in QUERIES.items():
        found, problem = extract_rules(policy_id, rule_type, find(query, policy_id), complete)
        rules += found
        if problem:
            skipped.append({"type": rule_type.value, "reason": problem})
    return {
        "policy_id": policy_id,
        "rules": [rule.model_dump(mode="json") for rule in _unique_ids(rules)],
        "skipped": skipped,
    }


def extract_rules(
    policy_id: str, rule_type: RuleType, hits: list[ClauseHit], complete: Complete
) -> tuple[list[Rule], str | None]:
    """Rules of one type from these clauses. Retries with the error, then gives up with a reason."""
    if not hits:
        return [], "no matching clause found"
    clauses = {hit.clause_id: hit for hit in hits}
    prompt = _prompt(rule_type, hits)
    problem = None
    for _ in range(MAX_ATTEMPTS):
        answer = complete(prompt if problem is None else f"{prompt}\n\nYour last answer was rejected: {problem}")
        try:
            return _parse(policy_id, rule_type, answer, clauses), None
        except ValueError as error:
            problem = str(error)
    return [], problem


def _parse(policy_id: str, rule_type: RuleType, answer: str, clauses: dict[str, ClauseHit]) -> list[Rule]:
    data = _json_object(answer)
    items = data.get("rules", [])
    if rule_type in _ONE_PER_POLICY and len(items) > 1:
        items = _stated_with_subject(rule_type, items, clauses)
    rules = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each rule must be a JSON object")
        item = _tidy(item)
        clause = _cited_clause(str(item.get("clause_id")), clauses)
        if clause is None:
            raise ValueError(f"clause_id must be one of {sorted(clauses)}, got {item.get('clause_id')!r}")
        try:
            rule = Rule.model_validate({
                **{k: v for k, v in item.items() if k in _RULE_FIELDS},
                "rule_id": f"{policy_id}-{clause.clause_id.replace(' ', '_')}-{rule_type.value}",
                "type": rule_type,
                "clause_id": clause.clause_id,
                "clause_text": clause.text,
                "verified_by_human": False,
            })
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(map(str, first["loc"]))
            allowed = ", ".join(VALUE_MODELS[rule_type].model_fields)
            raise ValueError(
                f"not a valid {rule_type.value} rule: {where}: {first['msg']} (value keys allowed: {allowed})"
            ) from None
        _check_against_clause(rule, clause)
        rules.append(rule)
    return rules


_RULE_FIELDS = {"value", "proportionate", "linked_charges", "excluded_from_proportionate"}
_ONE_PER_POLICY = {RuleType.ROOM_RENT_CAP, RuleType.ICU_CAP}
_EXCLUDES = re.compile(r"\bexcl|not covered|not payable|shall not be liable", re.I)
# A cap's numbers must be printed in the list item that names what it caps.
_CAP_SUBJECT = {RuleType.ROOM_RENT_CAP: re.compile(r"room", re.I), RuleType.ICU_CAP: re.compile(r"\bICU|intensive", re.I)}
_WAITING = re.compile(r"waiting period|months of continuous coverage", re.I)


def _stated_with_subject(
    rule_type: RuleType, items: list[Any], clauses: dict[str, ClauseHit]
) -> list[Any]:
    """Of several answers for a one-per-policy cap, the single one whose numbers sit in its own list item."""
    kept = []
    for item in items:
        if isinstance(item, dict) and (clause := _cited_clause(str(_tidy(item).get("clause_id")), clauses)):
            if _cap_item(rule_type, _numbers(_tidy(item)["value"]), clause.text):
                kept.append(item)
    if len(kept) != 1:
        raise ValueError(f"give exactly one {rule_type.value} rule, the main limit; got {len(items)}")
    return kept


def _cap_item(rule_type: RuleType, numbers: set[float], text: str) -> bool:
    """True if one list item of the clause names the cap's subject and prints all its numbers."""
    return any(
        _CAP_SUBJECT[rule_type].search(item) and numbers <= _numbers_in(item.replace(",", ""))
        for item in pieces(text)
    )


def _tidy(item: dict[str, Any]) -> dict[str, Any]:
    """Undo slips in the answer's layout that change nothing it says."""
    item = dict(item)
    value = dict(item.get("value") or {})
    for key in _RULE_FIELDS - {"value"}:
        if key in value:  # a rule-level field written inside value
            item.setdefault(key, value.pop(key))
    if isinstance(value.get("per"), str) and "day" in value["per"].lower():
        value["per"] = "day"
    item["value"] = value
    return item


def _cited_clause(cited: str, clauses: dict[str, ClauseHit]) -> ClauseHit | None:
    """The clause the answer cites; "4.1.1" or "Annexure A List I" count as the clause shown, 4.1 or Annexure A."""
    if cited in clauses:
        return clauses[cited]
    parents = [c for c in clauses if cited.startswith(c + ".") or cited.startswith(c + " ")]
    return clauses[parents[0]] if len(parents) == 1 else None


def _check_against_clause(rule: Rule, clause: ClauseHit) -> None:
    """Everything the rule says must be traceable to the clause it cites."""
    text = clause.text.replace(",", "")
    invented = _numbers(rule.value.model_dump()) - _numbers_in(text)
    if invented:
        raise ValueError(f"numbers not in clause {clause.clause_id}: {sorted(invented)}")
    if rule.type == RuleType.WAITING_PERIOD and not re.search(rf"\b{rule.value.months}\s*months?\b", text, re.I):
        raise ValueError(f"clause {clause.clause_id} does not say '{rule.value.months} months'; give months only")
    if rule.type in _CAP_SUBJECT and not _cap_item(rule.type, _numbers(rule.value.model_dump()), text):
        raise ValueError(f"clause {clause.clause_id} has no item about the {rule.type.value} that states these numbers")
    if rule.type == RuleType.EXCLUSION and (not _EXCLUDES.search(text) or _WAITING.search(text)):
        raise ValueError(f"clause {clause.clause_id} is not a permanent exclusion")
    names = {
        RuleType.SUB_LIMIT: lambda v: [v.procedure],
        RuleType.WAITING_PERIOD: lambda v: v.procedures,
        RuleType.EXCLUSION: lambda v: v.procedures,
    }.get(rule.type)
    if names:
        words = set(re.findall(r"[a-z]{4,}", text.lower()))
        unnamed = [n for n in names(rule.value) if not (set(n.split("_")) - _GENERIC_WORDS) & words]
        if unnamed:
            raise ValueError(f"clause {clause.clause_id} does not name: {', '.join(unnamed)}; list only what it names")


def _prompt(rule_type: RuleType, hits: list[ClauseHit]) -> str:
    clause_block = "\n\n".join(f"[clause {h.clause_id}] {h.text[:CLAUSE_CHARS]}" for h in hits)
    extra = ""
    if rule_type in (RuleType.ROOM_RENT_CAP, RuleType.ICU_CAP):
        extra = (
            '\nAlso give "proportionate": true if other charges are cut in proportion when the room'
            ' costs more than the limit, "linked_charges" (the charges that are cut) and'
            ' "excluded_from_proportionate" (the charges that are not), each a list of bill categories.'
            " If a limit is both a percent of sum insured and a rupee maximum, give both."
        )
    if rule_type in (RuleType.SUB_LIMIT, RuleType.WAITING_PERIOD, RuleType.EXCLUSION):
        extra = (
            "\nList only conditions or procedures the clause itself names, in snake_case. If one of these"
            " procedure names is how a named condition is treated, use it instead: "
            + ", ".join(sorted(BY_SLUG)) + ". Never copy this list."
        )
    if rule_type == RuleType.WAITING_PERIOD:
        extra += "\nGive one rule per waiting period stated in months; skip periods stated in days."
    if rule_type == RuleType.SUB_LIMIT:
        extra += "\nIgnore optional covers and tables of amounts that change with the sum insured."
    return (
        f"Rule type: {rule_type.value}\n"
        f"Value shape: {_VALUE_SHAPES[rule_type]}\n"
        f"Bill categories: {', '.join(c.value for c in BillCategory)}\n"
        f"{extra}\n\n"
        f'Answer as {{"rules": [{{"clause_id": "<id>", "value": {{...}}, ...}}]}}, one rule per limit the'
        f' clauses set, or {{"rules": []}} if they set none of this type.\n\n'
        f"Clauses:\n{clause_block}"
    )


def _json_object(answer: str) -> dict[str, Any]:
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end < start:
        raise ValueError("answer is not a JSON object")
    try:
        data = json.loads(answer[start: end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"answer is not valid JSON: {error}") from None
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
        raise ValueError('answer must look like {"rules": [...]}')
    return data


def _numbers(value: Any) -> set[float]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, int | float):
        return {float(value)}
    if isinstance(value, dict):
        return set().union(*map(_numbers, value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*map(_numbers, value)) if value else set()
    return set()


def _numbers_in(text: str) -> set[float]:
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))}


def _unique_ids(rules: list[Rule]) -> list[Rule]:
    seen: dict[str, int] = {}
    out = []
    for rule in rules:
        seen[rule.rule_id] = seen.get(rule.rule_id, 0) + 1
        if seen[rule.rule_id] > 1:
            rule = rule.model_copy(update={"rule_id": f"{rule.rule_id}-{seen[rule.rule_id]}"})
        out.append(rule)
    return out


def _llm(policy_id: str) -> Complete:
    seed = zlib.crc32(policy_id.encode())
    return lambda prompt: llm.complete(prompt, system=SYSTEM, seed=seed, temperature=0)


def main() -> None:
    from backend.rag.ingest import POLICY_DIR

    policy_ids = sys.argv[1:] or [p.stem for p in sorted(POLICY_DIR.glob("*.pdf"))]
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for policy_id in policy_ids:
            result = extract_policy(policy_id)
            (RULES_DIR / f"{policy_id}.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            skipped = ", ".join(s["type"] for s in result["skipped"]) or "none"
            print(f"{policy_id}: {len(result['rules'])} rules; nothing found for: {skipped}")
    except llm.LLMUnavailable as error:
        sys.exit(f"No LLM available. {error}")


if __name__ == "__main__":
    main()
