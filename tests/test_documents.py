"""Phase 3 checks: documents match their ground truth, missing documents stay missing, scans are images."""

import json
from pathlib import Path
from typing import Any

import fitz
import httpx
import pytest

from backend import llm
from backend.schemas import Case, Document, DocumentType
from data.generator import documents
from data.generator.admissions import ADMISSION_REASON, Generated
from data.generator.documents import expected_document_count, render_case, select_admissions
from data.generator.render import pdf_text

FIXTURES = sorted(Path("fixtures").glob("case*.json"))


def fake_writer(doc: Document, case: Case) -> str:
    return "Written without an LLM for the test."


def norm(text: str) -> str:
    return " ".join(text.replace(",", "").split())


def assert_pdf_matches_truth(pdf_path: Path) -> dict[str, Any]:
    truth = json.loads(pdf_path.with_suffix(".json").read_text(encoding="utf-8"))
    text = norm(pdf_text(pdf_path))
    values = [*truth["header"].values(), *(v for v in truth["fields"].values() if v is not None)]
    for line in truth.get("bill_lines", []):
        values += [line["description"], line["amount"]]
    if "narrative" in truth:
        values.append(truth["narrative"])
    for value in values:
        assert norm(str(value)) in text, f"{pdf_path.name}: {value!r} not printed"
    return truth


@pytest.fixture(scope="module")
def sample() -> list[Generated]:
    """A few admissions of each kind: clean, a missing document, a missing admission reason."""
    generated = select_admissions(None)
    missing_doc = [g for g in generated if g.detail and g.detail != ADMISSION_REASON][:4]
    no_reason = [g for g in generated if g.detail == ADMISSION_REASON][:2]
    clean = [g for g in generated if g.detail is None][:4]
    return clean + missing_doc + no_reason


@pytest.fixture(scope="module")
def rendered(sample: list[Generated], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, set[str]]:
    out = tmp_path_factory.mktemp("docs")
    scanned = {sample[0].case.documents[0].doc_id}
    for g in sample:
        render_case(g.case, out / g.case.admission.admission_id, fake_writer, scanned)
    return out, scanned


def test_count_is_required_minus_planted_missing(sample: list[Generated], rendered: tuple[Path, set[str]]) -> None:
    out, _ = rendered
    required, missing = expected_document_count(sample)
    assert missing == 4
    assert len(list(out.rglob("*.pdf"))) == len(list(out.rglob("*.json"))) == required - missing


def test_planted_missing_documents_are_not_written(sample: list[Generated], rendered: tuple[Path, set[str]]) -> None:
    out, _ = rendered
    for g in sample:
        types = {json.loads(p.read_text())["type"] for p in (out / g.case.admission.admission_id).glob("*.json")}
        if g.detail and g.detail != ADMISSION_REASON:
            assert g.detail not in types


def test_pdfs_match_ground_truth(rendered: tuple[Path, set[str]]) -> None:
    out, scanned = rendered
    for pdf in out.rglob("*.pdf"):
        if pdf.stem not in scanned:
            assert_pdf_matches_truth(pdf)


def test_missing_admission_reason_is_left_out(sample: list[Generated], rendered: tuple[Path, set[str]]) -> None:
    out, _ = rendered
    for g in (g for g in sample if g.detail == ADMISSION_REASON):
        summary = next(d for d in g.case.documents if d.type == DocumentType.DISCHARGE_SUMMARY)
        pdf = out / g.case.admission.admission_id / f"{summary.doc_id}.pdf"
        assert json.loads(pdf.with_suffix(".json").read_text())["fields"][ADMISSION_REASON] is None
        assert "reason for admission" not in pdf_text(pdf).lower()


def test_scans_are_image_only(rendered: tuple[Path, set[str]]) -> None:
    out, scanned = rendered
    [doc_id] = scanned
    [pdf] = out.rglob(f"{doc_id}.pdf")
    page = fitz.open(pdf)[0]
    assert page.get_text().strip() == ""
    assert len(page.get_images()) == 1
    assert json.loads(pdf.with_suffix(".json").read_text())["scanned"] is True


def test_sample_is_fixed() -> None:
    first = [g.case.case_id for g in select_admissions(20)]
    assert first == [g.case.case_id for g in select_admissions(20)]
    assert len(set(first)) == 20


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_demo_case_documents_are_committed_and_match(path: Path) -> None:
    case = Case.model_validate_json(path.read_text(encoding="utf-8"))
    for doc in case.documents:
        assert doc.file_path is not None
        truth = assert_pdf_matches_truth(Path(doc.file_path))
        assert truth["fields"] == doc.fields
        assert ("narrative" in truth) == (doc.type in {DocumentType.DISCHARGE_SUMMARY, DocumentType.LAB_REPORT})


def test_llm_writer_retries_when_numbers_are_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case.model_validate_json(FIXTURES[1].read_text(encoding="utf-8"))
    summary = next(d for d in case.documents if d.type == DocumentType.DISCHARGE_SUMMARY)
    replies = iter(["Discharged on day 9 after 250 mg twice daily.", "Recovered well over 4 days."])
    monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
    assert documents.llm_writer(summary, case) == "Recovered well over 4 days."

    monkeypatch.setattr(llm, "complete", lambda *a, **k: "Given 500 mg.")
    with pytest.raises(ValueError, match="numbers"):
        documents.llm_writer(summary, case)


def test_prompts_never_contain_the_patient_name(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(llm, "complete", lambda prompt, **k: prompts.append(prompt) or "Stable.")
    for path in FIXTURES:
        case = Case.model_validate_json(path.read_text(encoding="utf-8"))
        for doc in case.documents:
            if doc.type in {DocumentType.DISCHARGE_SUMMARY, DocumentType.LAB_REPORT}:
                documents.llm_writer(doc, case)
                assert case.patient.name.split()[0] not in prompts[-1]
    assert len(prompts) == 6


def _refuse(*args: Any) -> str:
    raise httpx.ConnectError("Ollama is not running")


def test_llm_falls_back_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_ollama", _refuse)
    monkeypatch.setattr(llm, "_gemini", lambda *a: "from gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert llm.complete("hi") == "from gemini"


def test_llm_unavailable_without_ollama_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_ollama", _refuse)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.LLMUnavailable, match="GEMINI_API_KEY"):
        llm.complete("hi")
