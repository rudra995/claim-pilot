"""Hidden strictness profiles of the three simulated TPAs (design section 5).

Hospital agents never import this. They have to learn these habits from past queries and denials.

The simulator can run with another set of habits without any code change: CLAIMPILOT_SIMULATOR=held_out
(or `using("held_out")`) swaps in HELD_OUT_PROFILES, which the denial model and the hospital agents
were never built against. The benchmark uses it to see how the hospital side copes with an insurer
it hasn't met. This is a simulator setting only; it never reaches the hospital side.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from backend.schemas import BillCategory, Rule

# What a TPA stops paying when the discharge summary doesn't say why admission was needed.
STAY_CHARGES = {BillCategory.ROOM, BillCategory.ICU, BillCategory.NURSING}


@dataclass(frozen=True)
class TpaProfile:
    tpa_id: str
    requires_xray_for_ortho: bool = False  # denies orthopaedic claims without the X-ray report
    denies_without_admission_reason: bool = False  # cuts stay charges if the summary doesn't say why
    proportionate_includes_pharmacy: bool = False  # also cuts medicines when the room is over cap

    def apply(self, rules: list[Rule]) -> list[Rule]:
        """The policy rules as this TPA applies them."""
        if not self.proportionate_includes_pharmacy:
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


TPA_PROFILES: dict[str, TpaProfile] = {
    "TPA-A": TpaProfile("TPA-A", requires_xray_for_ortho=True),
    "TPA-B": TpaProfile("TPA-B", denies_without_admission_reason=True),
    "TPA-C": TpaProfile("TPA-C", proportionate_includes_pharmacy=True),
}


# Habits the training data never had: TPA-A also cuts stays without a stated admission reason,
# TPA-B wants the X-ray for orthopaedic claims and cuts medicines with the room, TPA-C cuts stays.
HELD_OUT_PROFILES: dict[str, TpaProfile] = {
    "TPA-A": TpaProfile("TPA-A", requires_xray_for_ortho=True, denies_without_admission_reason=True),
    "TPA-B": TpaProfile("TPA-B", requires_xray_for_ortho=True, proportionate_includes_pharmacy=True),
    "TPA-C": TpaProfile("TPA-C", denies_without_admission_reason=True),
}
PROFILE_SETS = {"standard": TPA_PROFILES, "held_out": HELD_OUT_PROFILES}
_override: ContextVar[str | None] = ContextVar("simulator_profiles", default=None)


def active_set() -> str:
    name = _override.get() or os.environ.get("CLAIMPILOT_SIMULATOR", "standard")
    if name not in PROFILE_SETS:
        raise ValueError(f"Unknown simulator profile set '{name}'; use one of {sorted(PROFILE_SETS)}")
    return name


def profile_for(tpa_id: str) -> TpaProfile:
    """The simulated TPA's habits under the active profile set."""
    return PROFILE_SETS[active_set()][tpa_id]


@contextmanager
def using(name: str) -> Iterator[None]:
    """Run the simulator with another profile set inside this block."""
    if name not in PROFILE_SETS:
        raise ValueError(f"Unknown simulator profile set '{name}'")
    token = _override.set(name)
    try:
        yield
    finally:
        _override.reset(token)
