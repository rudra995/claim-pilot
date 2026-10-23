"""Print extracted rules for a person to check against the policy, and mark them verified.

    python -m backend.rag.review                         # every policy: rules and how many are verified
    python -m backend.rag.review POLICY_ID               # each rule with the clause it came from
    python -m backend.rag.review POLICY_ID --verify RULE_ID [RULE_ID ...]
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from backend.rag.extract import RULES_DIR
from backend.schemas import Rule

SNIPPET_CHARS = 160


def load(policy_id: str, rules_dir: Path = RULES_DIR) -> dict[str, Any]:
    return json.loads((rules_dir / f"{policy_id}.json").read_text(encoding="utf-8"))


def verify(policy_id: str, rule_ids: list[str], rules_dir: Path = RULES_DIR) -> list[str]:
    """Mark these rules verified. Returns any ids that aren't in the file (nothing is written then)."""
    data = load(policy_id, rules_dir)
    known = {rule["rule_id"] for rule in data["rules"]}
    unknown = [rule_id for rule_id in rule_ids if rule_id not in known]
    if unknown:
        return unknown
    for rule in data["rules"]:
        if rule["rule_id"] in rule_ids:
            rule["verified_by_human"] = True
            Rule.model_validate(rule)
    (rules_dir / f"{policy_id}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return []


def describe(rule: dict[str, Any]) -> str:
    """One rule as a person reads it, with the part of the clause its numbers come from."""
    mark = "verified" if rule["verified_by_human"] else "NOT VERIFIED"
    lines = [
        f"{rule['rule_id']}  [{mark}]",
        f"  {rule['type']}: {json.dumps(rule['value'], ensure_ascii=False)}",
    ]
    if rule.get("proportionate"):
        lines.append(
            f"  proportionate on {', '.join(rule['linked_charges'])};"
            f" not on {', '.join(rule['excluded_from_proportionate']) or 'nothing'}"
        )
    lines.append(f"  clause {rule['clause_id']}: \"{_snippet(rule)}\"")
    return "\n".join(lines)


def _snippet(rule: dict[str, Any]) -> str:
    text = rule["clause_text"]
    numbers = [str(v) for v in rule["value"].values() if isinstance(v, int | float) and not isinstance(v, bool)]
    positions = [m.start() for n in numbers for m in re.finditer(re.escape(n.removesuffix(".0")), text.replace(",", ""))]
    start = max(0, min(positions, default=0) - SNIPPET_CHARS // 3)
    return ("..." if start else "") + text[start: start + SNIPPET_CHARS].strip() + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("policy_id", nargs="?")
    parser.add_argument("--verify", nargs="+", metavar="RULE_ID")
    args = parser.parse_args()

    if args.policy_id is None:
        for path in sorted(RULES_DIR.glob("*.json")):
            rules = json.loads(path.read_text(encoding="utf-8"))["rules"]
            done = sum(rule["verified_by_human"] for rule in rules)
            print(f"{path.stem}: {len(rules)} rules, {done} verified")
        return
    if args.verify:
        unknown = verify(args.policy_id, args.verify)
        if unknown:
            raise SystemExit(f"Not in {args.policy_id}: {', '.join(unknown)}")
        print(f"Marked verified: {', '.join(args.verify)}")
        return
    data = load(args.policy_id)
    for rule in data["rules"]:
        print(describe(rule), end="\n\n")
    for skip in data["skipped"]:
        print(f"(no {skip['type']} rule: {skip['reason']})")


if __name__ == "__main__":
    main()
