"""Mask names, phone numbers and ID numbers before text goes to an LLM, and restore them after (design section 9).

The patient's name and IDs come from the case. Phone, Aadhaar and PAN numbers are caught by
pattern wherever they appear, case or no case. Each value gets its own token, so the reply can be
put back exactly.
"""

import re

from backend.schemas import Case

_PATTERNS = [
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
]


class Masker:
    def __init__(self, known: dict[str, str] | None = None) -> None:
        self._token_of: dict[str, str] = {}  # lower-cased original -> token
        self._original: dict[str, str] = {}  # token -> original
        for value, kind in (known or {}).items():
            self._token(value, kind)

    @classmethod
    def for_case(cls, case: Case) -> "Masker":
        """Knows the patient's full name, each part of it, and the patient and policy ids."""
        name = case.patient.name
        known = {name: "NAME"}
        known |= {part: "NAME" for part in name.split() if len(part) >= 3}
        known |= {case.patient.patient_id: "ID", case.policy.policy_id: "ID"}
        return cls(known)

    def mask(self, text: str) -> str:
        for kind, pattern in _PATTERNS:
            for found in pattern.findall(text):
                self._token(found, kind)
        for original in sorted(self._token_of, key=len, reverse=True):  # full name before its parts
            text = re.sub(rf"(?<!\w){re.escape(original)}(?!\w)", self._token_of[original], text, flags=re.IGNORECASE)
        return text

    def restore(self, text: str) -> str:
        for token, original in self._original.items():
            text = text.replace(token, original)
        return text

    def _token(self, value: str, kind: str) -> str:
        key = value.lower()
        if key not in self._token_of:
            token = f"[{kind}_{len(self._original) + 1}]"
            self._token_of[key] = token
            self._original[token] = value
        return self._token_of[key]
