"""Score the document reader, coder and evidence check on the independent challenge set.

    python -m backend.bench.challenge

The reader only ever sees fixtures/challenge/documents.json (file paths and types). The answers in
annotations.json are opened here, after reading, to score what came out.
"""

import json
import re
from pathlib import Path
from typing import Any

from backend.agents.hospital import coder
from backend.agents.hospital.intake import Reading, read_document
from backend.evidence.clinical import assess_admission_necessity
from backend.schemas import Admission, Document

FOLDER = Path("fixtures/challenge")


def _same(a: Any, b: Any) -> bool:
    return re.sub(r"\W+", " ", str(a)).strip().lower() == re.sub(r"\W+", " ", str(b)).strip().lower()


def score(folder: Path = FOLDER) -> dict[str, Any]:
    inputs = json.loads((folder / "documents.json").read_text(encoding="utf-8"))
    answers = json.loads((folder / "annotations.json").read_text(encoding="utf-8"))
    rows = []
    for item in inputs:
        doc = Document(doc_id=item["doc_id"], type=item["type"], file_path=item["file_path"])
        read = read_document(doc)
        truth = answers[item["doc_id"]]
        checks: list[tuple[str, bool]] = []
        code_outcome = None
        for name, expected in truth["fields"].items():
            field = read.fields.get(name)
            if expected == "ABSENT":
                checks.append((f"{name} abstained", field is None or field.value in (None, "")))
            elif expected == "CONFLICT":
                checks.append((f"{name} conflict flagged", field is not None and field.basis == "conflict"))
            elif expected == "PRESENT":
                checks.append((f"{name} found", field is not None and field.value not in (None, "")))
            else:
                checks.append((f"{name} correct", field is not None and _same(field.value, expected)))
        checks.append(("instructions flagged" if truth.get("instructions") else "no false instruction flag", bool(read.instructions) == bool(truth.get("instructions"))))
        checks.append(("scan sent to a person" if truth.get("unreadable") else "text read", bool(read.unreadable_pages) == bool(truth.get("unreadable"))))
        if "admission_necessity" in truth:
            assessment = assess_admission_necessity([doc], use_llm=False)
            checks.append((f"evidence {truth['admission_necessity']}", assessment.verdict == truth["admission_necessity"]))
            if "evidence_page" in truth:
                checks.append(("evidence page", any(p.page == truth["evidence_page"] for p in assessment.passages)))
        if "code" in truth:
            admission = Admission(
                admission_id=item["admission_id"], patient_id="P", policy_id="POL", procedure="unknown", icd10="R69",
                room_category="general_ward", admit_date="2026-01-01", discharge_date="2026-01-02", tpa_id="TPA-A",
            )
            result = coder.run("CHALLENGE", Reading(documents=[read]), admission)
            code_outcome = "ABSTAIN" if result.output.decision == "needs_review" else result.data.icd10
            checks.append((f"code {truth['code']}", code_outcome == truth["code"]))
        rows.append({
            "doc_id": item["doc_id"], "notes": truth.get("notes"), "code_outcome": code_outcome,
            "checks": [{"check": c, "passed": ok} for c, ok in checks],
        })

    field_checks = [c for r in rows for c in r["checks"] if c["check"].endswith(("correct", "found"))]
    abstain_checks = [c for r in rows for c in r["checks"] if c["check"].endswith(("abstained", "conflict flagged", "sent to a person")) or c["check"] == "code ABSTAIN"]
    all_checks = [c for r in rows for c in r["checks"]]
    return {
        "documents": len(rows),
        "checks": len(all_checks),
        "passed": sum(c["passed"] for c in all_checks),
        "field_accuracy": {"correct": sum(c["passed"] for c in field_checks), "of": len(field_checks)},
        "abstention": {"correct": sum(c["passed"] for c in abstain_checks), "of": len(abstain_checks)},
        "rows": rows,
    }


if __name__ == "__main__":
    result = score()
    for row in result["rows"]:
        failed = [c["check"] for c in row["checks"] if not c["passed"]]
        print(f"{row['doc_id']}: {'ok' if not failed else 'FAILED ' + ', '.join(failed)}  ({row['notes']})")
    print(f"{result['passed']}/{result['checks']} checks passed")
