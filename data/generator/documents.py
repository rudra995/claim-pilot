"""Write and render documents for synthetic admissions and the three demo cases (design section 8).

    python -m data.generator.documents [--sample 200 | --all] [--scans 50] [--db claimpilot.db]

The LLM (backend/llm.py) writes only the prose: the hospital course of a discharge summary and the
remarks on a lab report. Every field is printed from the structured data, and each PDF gets a
ground-truth JSON next to it with the true values. Documents planted as missing are never written.
A rerun skips documents already on disk, so a stopped run picks up where it left off.

Demo-case documents go to fixtures/documents/ (committed); synthetic ones to data/documents/.
"""

import argparse
import json
import random
import re
import sqlite3
import sys
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import llm
from backend.schemas import Case, Document, DocumentType
from data.generator.admissions import ADMISSION_REASON, Generated, required_documents
from data.generator.generate import SEED, generate
from data.generator.procedures import BY_SLUG
from data.generator.render import NARRATIVE_HEADINGS, header_fields, make_scan, render_pdf

OUT_DIR = Path("data/documents")
FIXTURE_DIR = Path("fixtures")
FIXTURE_DOC_DIR = FIXTURE_DIR / "documents"
SAMPLE_SIZE = 200
N_SCANS = 50
MAX_ATTEMPTS = 4

Writer = Callable[[Document, Case], str]

SYSTEM = (
    "You write single sections of hospital clinical documents. Write plain prose only: no markdown,"
    " no headings, no lists. Use only the facts given. Do not add any number, date, dose or test value"
    " that is not in the facts. Do not name the patient; say 'the patient'."
)
_GENDER = {"M": "male", "F": "female", "O": "patient"}


def llm_writer(doc: Document, case: Case) -> str:
    """The prose section of a discharge summary or lab report, written by the LLM."""
    prompt = _prompt(doc, case)
    allowed = _numbers(prompt)
    seed = _seed(doc.doc_id)
    for attempt in range(MAX_ATTEMPTS):
        text = _clean(llm.complete(prompt, system=SYSTEM, seed=seed + attempt))
        if text and _numbers(text) <= allowed:
            return text
    raise ValueError(f"{doc.doc_id}: the LLM kept adding numbers that are not in the record")


def ground_truth(doc: Document, case: Case, scanned: bool, narrative: str | None) -> dict[str, Any]:
    truth: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "admission_id": case.admission.admission_id,
        "type": doc.type.value,
        "scanned": scanned,
        "header": header_fields(doc, case),
        "fields": doc.fields,
    }
    if doc.type == DocumentType.FINAL_BILL:
        truth["bill_lines"] = [line.model_dump(mode="json") for line in case.bill_lines]
    if narrative is not None:
        truth["narrative"] = narrative
    return truth


def render_case(case: Case, out_dir: Path, writer: Writer, scanned_ids: set[str] = frozenset()) -> list[Path]:
    """Write every document the case has as PDF + ground-truth JSON. Returns the PDF paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for doc in case.documents:
        pdf_path, truth_path = out_dir / f"{doc.doc_id}.pdf", out_dir / f"{doc.doc_id}.json"
        paths.append(pdf_path)
        if pdf_path.exists() and truth_path.exists():
            continue
        narrative = writer(doc, case) if doc.type in NARRATIVE_HEADINGS else None
        pdf = render_pdf(doc, case, narrative)
        scanned = doc.doc_id in scanned_ids
        if scanned:
            pdf = make_scan(pdf, _seed(doc.doc_id))
        pdf_path.write_bytes(pdf)
        # Written last, so a document only counts as done once both files exist.
        truth_path.write_text(json.dumps(ground_truth(doc, case, scanned, narrative), indent=2), encoding="utf-8")
    return paths


def select_admissions(sample: int | None, seed: int = SEED) -> list[Generated]:
    """A fixed random sample of the generated admissions (all of them if sample is None)."""
    generated = [g for g, _ in generate()]
    if sample is None or sample >= len(generated):
        return generated
    return sorted(random.Random(seed).sample(generated, sample), key=lambda g: g.case.case_id)


def pick_scans(cases: list[Case], n: int, seed: int = SEED) -> set[str]:
    doc_ids = sorted(doc.doc_id for case in cases for doc in case.documents)
    return set(random.Random(seed).sample(doc_ids, min(n, len(doc_ids))))


def expected_document_count(generated: list[Generated]) -> tuple[int, int]:
    """(documents required, documents planted as missing) across these admissions."""
    required = sum(len(required_documents(BY_SLUG[g.case.admission.procedure])) for g in generated)
    missing = sum(1 for g in generated if g.detail is not None and g.detail != ADMISSION_REASON)
    return required, missing


def _prompt(doc: Document, case: Case) -> str:
    patient = f"{case.patient.age}-year-old {_GENDER[case.patient.gender]}"
    if doc.type == DocumentType.DISCHARGE_SUMMARY:
        f = doc.fields
        facts = [
            f"Patient: {patient}",
            f"Diagnosis: {f['diagnosis']}",
            f"Treatment given: {f['treatment_given']}",
            f"Length of stay: {f['length_of_stay_days']} days",
            f"Condition at discharge: {f['condition_at_discharge']}",
        ]
        if f.get(ADMISSION_REASON):
            facts.insert(2, f"Why admission was needed: {f[ADMISSION_REASON]}")
            rule = "State why admission was needed."
        else:
            rule = "Do not say why the patient was admitted or why admission was needed."
        return (
            "Write the 'Hospital course' section of a discharge summary in 60 to 110 words. "
            f"{rule}\n\nFacts:\n" + "\n".join(facts)
        )
    if doc.type == DocumentType.LAB_REPORT:
        results = "\n".join(f"{key.replace('_', ' ')}: {value}" for key, value in doc.fields.items())
        return (
            "Write the pathologist's remarks for a laboratory report in 2 to 4 sentences. "
            "Interpret the results given; do not add new results.\n\n"
            f"Patient: {patient}\nAdmitted for: {_diagnosis(case)}\n"
            f"Results:\n{results}"
        )
    raise ValueError(f"no prose section for {doc.type}")


def _diagnosis(case: Case) -> str:
    for doc in case.documents:
        for key in ("diagnosis", "provisional_diagnosis"):
            if doc.fields.get(key):
                return doc.fields[key]
    procedure = BY_SLUG.get(case.admission.procedure)
    return procedure.diagnosis if procedure else case.admission.procedure.replace("_", " ")


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def _clean(text: str) -> str:
    text = re.sub(r"```\w*", "", text)
    text = re.sub(r"[*#_`]", "", text)
    text = re.sub(r"^\s*(hospital course|remarks|pathologist'?s remarks)\s*:?\s*", "", text, flags=re.I)
    return " ".join(text.split())


def _seed(doc_id: str) -> int:
    return zlib.crc32(doc_id.encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    size = parser.add_mutually_exclusive_group()
    size.add_argument("--sample", type=int, default=SAMPLE_SIZE, help="admissions to write documents for")
    size.add_argument("--all", action="store_true", help="every generated admission")
    parser.add_argument("--scans", type=int, default=N_SCANS)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--db", type=Path, default=Path("claimpilot.db"), help="updated with file paths if it exists")
    args = parser.parse_args()

    try:
        for path in sorted(FIXTURE_DIR.glob("case*.json")):
            case = Case.model_validate_json(path.read_text(encoding="utf-8"))
            render_case(case, FIXTURE_DOC_DIR / case.admission.admission_id, llm_writer)
            print(f"{path.name}: {len(case.documents)} documents")

        generated = select_admissions(None if args.all else args.sample)
        scanned = pick_scans([g.case for g in generated], args.scans)
        written: list[Path] = []
        for i, g in enumerate(generated, start=1):
            written += render_case(g.case, args.out / g.case.admission.admission_id, llm_writer, scanned)
            if i % 10 == 0 or i == len(generated):
                print(f"  {i}/{len(generated)} admissions", flush=True)
    except llm.LLMUnavailable as error:
        sys.exit(f"No LLM available, nothing more written. {error}")

    if args.db.exists():
        with sqlite3.connect(args.db) as conn:
            conn.executemany(
                "UPDATE documents SET file_path = ? WHERE doc_id = ?",
                [(path.as_posix(), path.stem) for path in written],
            )

    required, missing = expected_document_count(generated)
    on_disk = sum(1 for g in generated for _ in (args.out / g.case.admission.admission_id).glob("*.pdf"))
    print(f"Admissions: {len(generated)}; documents required {required}, planted missing {missing}")
    print(f"PDFs on disk: {on_disk} (expected {required - missing}); scans: {len(scanned)}")
    if on_disk != required - missing:
        sys.exit("Document count does not match")


if __name__ == "__main__":
    main()
