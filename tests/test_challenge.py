"""Refinement of phase 6: reading documents this hospital didn't print, and abstaining when unsure."""

import json
from pathlib import Path

from backend.bench.challenge import FOLDER, score

HELD_OUT = FOLDER / "held_out"


def test_runtime_inputs_hold_no_answers() -> None:
    for folder in (FOLDER, HELD_OUT):
        for item in json.loads((folder / "documents.json").read_text(encoding="utf-8")):
            assert set(item) == {"doc_id", "type", "admission_id", "file_path"}


def test_challenge_documents_are_not_made_by_the_generator() -> None:
    source = Path("data/challenge/build.py").read_text(encoding="utf-8")
    assert "data.generator" not in source and "render_pdf" not in source


def test_development_challenge_set_passes_every_check() -> None:
    """Narrative text, other headings, negation, missing and conflicting fields, an unrelated report,
    an instruction to bypass approval, a scan and a two-page report (used while building the reader)."""
    result = score()
    failed = [(row["doc_id"], c["check"]) for row in result["rows"] for c in row["checks"] if not c["passed"]]
    assert failed == []


def test_held_out_set_has_no_unsafe_answers() -> None:
    """Scored as written, not tuned: a miss must be an abstention, never a wrong code or a missed instruction."""
    result = score(HELD_OUT)
    for row in result["rows"]:
        for check in row["checks"]:
            if not check["passed"]:
                assert check["check"].startswith("code ") and row["code_outcome"] == "ABSTAIN", f"{row['doc_id']}: {check['check']}"
    assert result["field_accuracy"]["correct"] == result["field_accuracy"]["of"]
