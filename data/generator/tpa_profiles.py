"""Hidden strictness profiles of the three simulated TPAs (design section 5).

Hospital agents never import this. They have to learn these habits from past queries and denials.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TpaProfile:
    tpa_id: str
    requires_xray_for_ortho: bool = False  # denies orthopaedic claims without the X-ray report
    denies_without_admission_reason: bool = False  # cuts stay charges if the summary doesn't say why
    proportionate_includes_pharmacy: bool = False  # also cuts medicines when the room is over cap


TPA_PROFILES: dict[str, TpaProfile] = {
    "TPA-A": TpaProfile("TPA-A", requires_xray_for_ortho=True),
    "TPA-B": TpaProfile("TPA-B", denies_without_admission_reason=True),
    "TPA-C": TpaProfile("TPA-C", proportionate_includes_pharmacy=True),
}
