"""Document reader: pulls the labelled fields out of each claim document, with the page each came from.

Text PDFs are read from their text layer. Only the "Details" block is read, never the patient
header, so names and ID numbers don't travel on to other agents. A page with no text layer (a
scan) gives zero-confidence fields and asks a person to read it.
"""

import re
from pathlib import Path

import fitz
from pydantic import BaseModel, Field

from backend.agents.base import HUMAN, AgentResult
from backend.schemas import AgentOutput, Document, DocumentType, Finding
from data.generator.render import NARRATIVE_HEADINGS, SYNTHETIC_NOTICE, TITLES

AGENT = "document_reader"
MIN_CONFIDENCE = 0.8  # below this a person checks the field (design section 4)
TEXT_LAYER_CONFIDENCE = 0.95
NOT_FOUND_CONFIDENCE = 0.5

# Fields the later agents rely on, per document type.
EXPECTED: dict[DocumentType, list[str]] = {
    DocumentType.ADMISSION_NOTE: ["chief_complaint", "provisional_diagnosis", "planned_treatment"],
    DocumentType.LAB_REPORT: ["test"],
    DocumentType.XRAY_REPORT: ["finding"],
    DocumentType.OPERATIVE_NOTE: ["procedure"],
    DocumentType.DISCHARGE_SUMMARY: ["diagnosis", "icd10", "reason_for_admission", "treatment_given"],
    DocumentType.FINAL_BILL: ["total_billed"],
}

_FIELD_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 /()-]*?):\s*(.*)$")
_STOP_LINES = {SYNTHETIC_NOTICE, *NARRATIVE_HEADINGS.values()}


class ReadField(BaseModel):
    value: str | int | None
    page: int  # 1-based
    confidence: float = Field(ge=0.0, le=1.0)


class ReadDocument(BaseModel):
    doc_id: str
    type: DocumentType
    fields: dict[str, ReadField]
    not_found: list[str]  # expected fields with no line in the document

    def source(self, page: int = 1) -> str:
        return f"{TITLES[self.type]} {self.doc_id} p.{page}"


class Reading(BaseModel):
    documents: list[ReadDocument]

    def get(self, doc_type: DocumentType) -> ReadDocument | None:
        return next((d for d in self.documents if d.type == doc_type), None)

    def value(self, doc_type: DocumentType, name: str) -> str | int | None:
        doc = self.get(doc_type)
        field = doc.fields.get(name) if doc else None
        return field.value if field else None


def run(claim_id: str, documents: list[Document]) -> AgentResult:
    read = [read_document(doc) for doc in documents]
    findings: list[Finding] = []
    for doc in read:
        if doc.fields:
            names = ", ".join(name.replace("_", " ") for name in doc.fields)
            findings.append(
                Finding(issue=f"{TITLES[doc.type]} read", evidence=f"Fields: {names}", source=doc.source())
            )
        for name, field in doc.fields.items():
            if field.confidence < MIN_CONFIDENCE:
                findings.append(
                    Finding(
                        issue=f"Low confidence: {name.replace('_', ' ')}",
                        evidence=f"Read with confidence {field.confidence:.2f}; needs a person to check",
                        source=doc.source(field.page),
                    )
                )
            elif field.value in (None, ""):
                findings.append(
                    Finding(issue=f"Blank field: {name.replace('_', ' ')}", evidence="The label is there but empty", source=doc.source(field.page))
                )
        for name in doc.not_found:
            label = name.replace("_", " ")
            findings.append(
                Finding(issue=f"Not found: {label}", evidence=f"No '{label}' line in the document", source=doc.source())
            )

    confidences = [f.confidence for d in read for f in d.fields.values()]
    confidences += [NOT_FOUND_CONFIDENCE for d in read for _ in d.not_found]
    confidence = min(confidences, default=0.0)
    needs_person = confidence < MIN_CONFIDENCE
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="needs_review" if needs_person else "read",
            confidence=confidence,
            findings=findings,
            next=HUMAN if needs_person else "clinical_coder",
        ),
        data=Reading(documents=read),
    )


def read_document(doc: Document) -> ReadDocument:
    if doc.file_path is None or not Path(doc.file_path).exists():
        raise FileNotFoundError(f"{doc.doc_id}: no file at {doc.file_path}")
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    fields: dict[str, ReadField] = {}
    with fitz.open(doc.file_path) as pdf:
        for number, page in enumerate(pdf, start=1):
            text = page.get_text(flags=flags)
            if not text.strip():  # a scan: nothing to read without OCR, so a person reads it
                for name in EXPECTED.get(doc.type, []):
                    fields.setdefault(name, ReadField(value=None, page=number, confidence=0.0))
                continue
            parsed = _bill_total(text) if doc.type == DocumentType.FINAL_BILL else _details(text)
            for name, value in parsed.items():
                fields.setdefault(name, ReadField(value=value, page=number, confidence=TEXT_LAYER_CONFIDENCE))
    not_found = [name for name in EXPECTED.get(doc.type, []) if name not in fields]
    return ReadDocument(doc_id=doc.doc_id, type=doc.type, fields=fields, not_found=not_found)


def _details(text: str) -> dict[str, str]:
    """`Label: value` lines from the Details block; a wrapped value continues on the next line."""
    fields: dict[str, str] = {}
    in_details = False
    last = None
    for line in (line.strip() for line in text.splitlines()):
        if line == "Details":
            in_details = True
        elif not in_details or not line:
            continue
        elif line in _STOP_LINES:
            break
        elif match := _FIELD_LINE.match(line):
            last = _key(match[1])
            fields[last] = match[2].strip()
        elif last is not None:
            fields[last] = f"{fields[last]} {line}".strip()
    return fields


def _bill_total(text: str) -> dict[str, int]:
    """The final bill's total: the amount on the line after "Total billed", e.g. "Rs. 2,40,000"."""
    lines = [line.strip() for line in text.splitlines()]
    for label, value in zip(lines, lines[1:]):
        if label == "Total billed" and (digits := re.sub(r"\D", "", value)):
            return {"total_billed": int(digits)}
    return {}


def _key(label: str) -> str:
    """'Wbc per ul' -> 'wbc_per_ul', 'ICD-10' -> 'icd10'."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower().replace("-", "")).strip("_")
