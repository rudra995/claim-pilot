"""The case file behind each claim, as the API last saw it.

Agents need the whole case (bill, documents, policy) at every step. The API writes the case to the
audit log when a claim starts and again whenever staff change it (new bill lines), so the latest
version can always be read back, and the change itself is on the record. A claim opened outside the
API (the run_case CLI) has no case file yet, so its fixture is found from the case_id it was opened with.
"""

from pathlib import Path
from typing import Any

from backend.db import audit
from backend.schemas import Case

FIXTURES = Path("fixtures")
ACTOR = "api"


def fixture_cases() -> dict[str, Case]:
    """The demo cases shipped in fixtures/, by case_id."""
    cases = [Case.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("case*.json"))]
    return {case.case_id: case for case in cases}


def save(db_path: Path | str, claim_id: str, case: Case, reason: str) -> None:
    audit.log(db_path, claim_id, ACTOR, "case_file", {"reason": reason, "case": case.model_dump(mode="json")})


def load(db_path: Path | str, claim_id: str) -> Case:
    case = from_events(audit.events(db_path, claim_id))
    if case is None:
        raise KeyError(f"No case file for {claim_id}")
    return case


def from_events(events: list[dict[str, Any]]) -> Case | None:
    """The latest case file in a claim's audit trail, or the fixture it was opened with."""
    files = [e["output"]["case"] for e in events if e["action"] == "case_file"]
    if files:
        return Case.model_validate(files[-1])
    opened = next((e["output"]["case_id"] for e in events if e["action"] == "open_claim"), None)
    return fixture_cases().get(opened)
