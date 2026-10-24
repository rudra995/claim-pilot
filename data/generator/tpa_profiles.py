"""Hidden strictness profiles of the three simulated TPAs (design section 5).

Hospital agents never import this. They have to learn these habits from past queries and denials.
"""

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
