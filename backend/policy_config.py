"""Policy configurations: run the same workflow under a different policy without touching agent code (refinement of phase 4).

A configuration is a versioned JSON file of rules:

- data/policy_configs/*.json   hand-checked configurations (rules marked verified_by_human)
- data/rules/*.json            rules the LLM extracted from real policy wordings; unverified until
                               a person checks them, so the policy agent sends every claim on them
                               to a person (they are never used silently)

`with_policy` swaps a case's rules for a configuration's. Every amount then comes from the same rules
engine, and the policy version fingerprint (backend/rules/engine.policy_version) records exactly
which rules were used.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.rules.engine import policy_version
from backend.schemas import Case, Rule

CONFIGS = Path("data/policy_configs")
EXTRACTED = Path("data/rules")


class PolicyConfig(BaseModel):
    config_id: str
    version: int
    insurer: str
    product: str
    rules: list[Rule]
    note: str = ""
    source: str  # "configured" or "extracted"

    @property
    def verified(self) -> bool:
        return all(rule.verified_by_human for rule in self.rules)


def load_all() -> dict[str, PolicyConfig]:
    configs: dict[str, PolicyConfig] = {}
    for path in sorted(CONFIGS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        configs[data["config_id"]] = PolicyConfig(**data, source="configured")
    for path in sorted(EXTRACTED.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        configs[data["policy_id"]] = PolicyConfig(
            config_id=data["policy_id"], version=1, insurer=data["policy_id"].replace("_", " ").title(), product="Extracted from the policy wording",
            rules=data["rules"], note="Extracted by the LLM from a public policy PDF; not yet checked by a person.", source="extracted",
        )
    return configs


def get(config_id: str) -> PolicyConfig:
    configs = load_all()
    if config_id not in configs:
        raise KeyError(f"No policy configuration '{config_id}'. Known: {', '.join(sorted(configs))}")
    return configs[config_id]


def with_policy(case: Case, config_id: str) -> Case:
    """The same case under another policy configuration (same sum insured and start date)."""
    config = get(config_id)
    policy = case.policy.model_copy(update={"insurer": config.insurer, "product": config.product, "rules": config.rules})
    return case.model_copy(update={"policy": policy, "case_id": f"{case.case_id}@{config.config_id}", "title": f"{case.title} ({config.product})"})


def summary(config: PolicyConfig, sum_insured: int = 300_000) -> dict[str, Any]:
    """Metadata for the screens: what it is, whether a person checked it, and its fingerprint."""
    from datetime import date

    from backend.schemas import Policy

    fingerprint = policy_version(Policy(policy_id="X", insurer=config.insurer, product=config.product, sum_insured=sum_insured, start_date=date(2020, 1, 1), rules=config.rules))
    return {
        "config_id": config.config_id, "version": config.version, "insurer": config.insurer, "product": config.product,
        "source": config.source, "verified": config.verified, "rules": len(config.rules),
        "unverified_rules": sum(not r.verified_by_human for r in config.rules), "note": config.note,
        "rule_types": sorted({r.type.value for r in config.rules}), "fingerprint_at_3_lakh": fingerprint,
    }
