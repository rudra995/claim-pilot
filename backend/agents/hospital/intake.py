"""Document reader: pulls the clinical fields out of each claim document, with the page and the text each came from.

Text PDFs are read from their text layer, in three passes, most reliable first:

1. labelled fields in our own "Details" block (the fast path for documents this hospital prints);
2. the same fields under other common labels or headings ("Presenting complaint", "IMPRESSION",
   "Indication for admission"), anywhere on the page, so other hospitals' layouts can be read;
3. for the reason for admission only, a narrative sentence ("she was admitted for IV fluids").

The patient header (name, ID numbers) is never read, so it doesn't travel on to other agents. The
document's text is untrusted data: an instruction inside it ("approve this claim") is flagged for a
person and never acted on.

`confidence` on a field is a fixed tier for how the value was found (`basis`), not a calibrated
probability. Below MIN_CONFIDENCE a person checks it. Two different values for one field are a
conflict: the reader keeps neither and asks a person. A page with no text layer (a scan) can't be
read here (there is no OCR), so its fields are left blank and a person reads it.
"""

import re
from pathlib import Path
from typing import Literal

import fitz
from pydantic import BaseModel, Field

from backend.agents.base import HUMAN, AgentResult
from backend.evidence.text import instructions_in
from backend.schemas import AgentOutput, Document, DocumentType, Finding
from data.generator.render import NARRATIVE_HEADINGS, SYNTHETIC_NOTICE, TITLES

AGENT = "document_reader"
MIN_CONFIDENCE = 0.8  # below this a person checks the field (design section 4)
TEXT_LAYER_CONFIDENCE = 0.95
NOT_FOUND_CONFIDENCE = 0.5
TIER = {"corrected": 1.0, "labelled": 0.95, "alternate_label": 0.85, "heading": 0.85, "narrative": 0.6, "unreadable": 0.0, "conflict": 0.0}
Basis = Literal["corrected", "labelled", "alternate_label", "heading", "narrative", "unreadable", "conflict"]

# Fields the later agents rely on, per document type.
EXPECTED: dict[DocumentType, list[str]] = {
    DocumentType.ADMISSION_NOTE: ["chief_complaint", "provisional_diagnosis", "planned_treatment"],
    DocumentType.LAB_REPORT: ["test"],
    DocumentType.XRAY_REPORT: ["finding"],
    DocumentType.OPERATIVE_NOTE: ["procedure"],
    DocumentType.DISCHARGE_SUMMARY: ["diagnosis", "icd10", "reason_for_admission", "treatment_given"],
    DocumentType.FINAL_BILL: ["total_billed"],
}

# Other labels or headings that mean the same field, per document type.
SYNONYMS: dict[DocumentType, dict[str, list[str]]] = {
    DocumentType.ADMISSION_NOTE: {
        "chief_complaint": ["chief complaint", "chief complaints", "presenting complaint", "presenting complaints", "complaints", "c/o", "presenting symptoms", "history of presenting illness"],
        "provisional_diagnosis": ["provisional diagnosis", "working diagnosis", "admitting diagnosis", "diagnosis on admission", "impression", "diagnosis"],
        "planned_treatment": ["planned treatment", "treatment plan", "plan", "management plan", "proposed procedure", "plan of care"],
        "vitals": ["vitals", "vital signs", "on examination", "general examination"],
    },
    DocumentType.LAB_REPORT: {
        "test": ["test", "tests", "investigation", "investigations", "test name", "investigations done"],
        "finding": ["finding", "findings", "result", "results"],
        "conclusion": ["interpretation", "conclusion"],
    },
    DocumentType.XRAY_REPORT: {  # findings and impression are separate sections of a report, not two takes on one
        "finding": ["finding", "findings", "report", "radiological findings"],
        "impression": ["impression", "conclusion", "opinion"],
    },
    DocumentType.OPERATIVE_NOTE: {
        "procedure": ["procedure", "procedure performed", "operation", "operation performed", "name of operation", "surgery performed"],
        "implant": ["implant", "implants used", "prosthesis"],
    },
    DocumentType.DISCHARGE_SUMMARY: {
        "diagnosis": ["diagnosis", "final diagnosis", "discharge diagnosis", "diagnosis at discharge"],
        "icd10": ["icd-10", "icd10", "icd code", "icd-10 code", "icd 10"],
        "reason_for_admission": ["reason for admission", "indication for admission", "reason for hospitalisation", "reason for hospitalization", "why admitted"],
        "treatment_given": ["treatment given", "treatment", "course of treatment", "management", "procedures done"],
    },
    DocumentType.FINAL_BILL: {"total_billed": ["total billed", "grand total", "total amount", "net amount", "total"]},
}

_FIELD_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 /()-]*?):\s*(.*)$")
_STOP_LINES = {SYNTHETIC_NOTICE, *NARRATIVE_HEADINGS.values()}
_LABELLED = re.compile(r"^([A-Za-z][A-Za-z0-9 /()'-]{0,40}?)(?:\s*:\s*|\s+[-–]\s+)(.*)$")
_NOTICE_LINE = re.compile(r"synthetic (test )?document|not a real patient record", re.IGNORECASE)
_ADMITTED_FOR = re.compile(r"\b(?:was|were|is)?\s*admitted\s+(?:because of|because|for|with|due to)\s+([^.]{4,200})", re.IGNORECASE)


class ReadField(BaseModel):
    value: str | int | None
    page: int  # 1-based
    confidence: float = Field(ge=0.0, le=1.0)  # a tier for `basis`, not a calibrated probability
    basis: Basis = "labelled"
    text: str | None = None  # the line or sentence it was read from
    conflict: list[str] = Field(default_factory=list)  # the competing values, when basis is "conflict"


class ReadDocument(BaseModel):
    doc_id: str
    type: DocumentType
    fields: dict[str, ReadField]
    not_found: list[str]  # expected fields with no line in the document
    unreadable_pages: list[int] = Field(default_factory=list)  # scans: no text layer, no OCR here
    instructions: list[str] = Field(default_factory=list)  # instruction-like text found, never obeyed

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


def with_corrections(reading: Reading, corrections: list[dict]) -> Reading:
    """The reading with each person's recorded correction applied on top; the document itself is untouched."""
    docs = {doc.doc_id: doc.model_copy(deep=True) for doc in reading.documents}
    for fix in corrections:
        doc = docs.get(fix["doc_id"])
        if doc is None:
            continue
        page = doc.fields[fix["field"]].page if fix["field"] in doc.fields else 1
        doc.fields[fix["field"]] = ReadField(
            value=fix["value"], page=page, confidence=TIER["corrected"], basis="corrected",
            text=f"Corrected by {fix['officer']}: {fix['reason']}"[:200],
        )
        doc.not_found = [name for name in doc.not_found if name != fix["field"]]
    return Reading(documents=list(docs.values()))


def run(claim_id: str, documents: list[Document], corrections: list[dict] | None = None) -> AgentResult:
    read = with_corrections(Reading(documents=[read_document(doc) for doc in documents]), corrections or []).documents
    findings: list[Finding] = []
    for doc in read:
        if doc.fields:
            names = ", ".join(name.replace("_", " ") for name, f in doc.fields.items() if f.value not in (None, ""))
            findings.append(Finding(issue=f"{TITLES[doc.type]} read", evidence=f"Fields: {names or 'none readable'}", source=doc.source()))
        for page in doc.unreadable_pages:
            findings.append(Finding(
                issue="Scanned page can't be read",
                evidence="No text layer and no OCR in this build: a person reads it, or attaches a text copy",
                source=doc.source(page),
            ))
        for name, field in doc.fields.items():
            label = name.replace("_", " ")
            if field.basis == "conflict":
                findings.append(Finding(issue=f"Conflicting values: {label}", evidence=" vs ".join(field.conflict) + "; neither is used until a person picks one", source=doc.source(field.page)))
            elif field.basis == "unreadable":
                continue
            elif field.confidence < MIN_CONFIDENCE:
                findings.append(Finding(issue=f"Check: {label}", evidence=f"Read from {field.basis.replace('_', ' ')} text, not a labelled field: \"{field.text}\"", source=doc.source(field.page)))
            elif field.value in (None, ""):
                findings.append(Finding(issue=f"Blank field: {label}", evidence="The label is there but empty", source=doc.source(field.page)))
        for name in doc.not_found:
            label = name.replace("_", " ")
            findings.append(Finding(issue=f"Not found: {label}", evidence=f"No '{label}' in the document; not guessed", source=doc.source()))
        for phrase in doc.instructions:
            findings.append(Finding(
                issue="Instruction found in a document",
                evidence=f"\"{phrase}\": document text is data, so it changes nothing; a person should look at this document",
                source=doc.source(),
            ))

    confidences = [f.confidence for d in read for f in d.fields.values()]
    confidences += [NOT_FOUND_CONFIDENCE for d in read for _ in d.not_found]
    confidence = min(confidences, default=0.0)
    needs_person = confidence < MIN_CONFIDENCE or any(d.instructions for d in read)
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
    candidates: dict[str, list[ReadField]] = {}
    unreadable, pages = [], []
    with fitz.open(doc.file_path) as pdf:
        for number, page in enumerate(pdf, start=1):
            text = page.get_text(flags=flags)
            pages.append(text)
            if not text.strip():
                unreadable.append(number)
                continue
            for name, field in _candidates(doc.type, text, number):
                candidates.setdefault(name, []).append(field)

    fields = {name: _settle(found) for name, found in candidates.items()}
    if unreadable:  # a scan: nothing to read without OCR, so a person reads it
        for name in EXPECTED.get(doc.type, []):
            fields.setdefault(name, ReadField(value=None, page=unreadable[0], confidence=0.0, basis="unreadable"))
    not_found = [name for name in EXPECTED.get(doc.type, []) if name not in fields]
    instructions = sorted({phrase for _, phrase in instructions_in(pages)})
    return ReadDocument(doc_id=doc.doc_id, type=doc.type, fields=fields, not_found=not_found, unreadable_pages=unreadable, instructions=instructions)


def _candidates(doc_type: DocumentType, text: str, page: int) -> list[tuple[str, ReadField]]:
    """Every value found for every field on a page, with how it was found."""
    if doc_type == DocumentType.FINAL_BILL:
        total = _bill_total(text)
        if total:
            return [("total_billed", ReadField(value=total["total_billed"], page=page, confidence=TIER["labelled"], text="Total billed"))]
    found: list[tuple[str, ReadField]] = []
    details = _details(text)
    for name, value in details.items():
        found.append((name, ReadField(value=value, page=page, confidence=TIER["labelled"], basis="labelled", text=f"{name}: {value}"[:200])))
    labelled = set(details)
    for name, value, basis, line in _alternate(doc_type, text):
        if name not in labelled:
            found.append((name, ReadField(value=value, page=page, confidence=TIER[basis], basis=basis, text=line[:200])))
    if doc_type == DocumentType.XRAY_REPORT and not any(n == "finding" for n, _ in found):
        found += [("finding", field) for n, field in list(found) if n == "impression"]  # a report with only an impression
    if doc_type == DocumentType.DISCHARGE_SUMMARY and not any(n == "reason_for_admission" for n, _ in found):
        if match := _ADMITTED_FOR.search(re.sub(r"\s+", " ", text)):
            found.append(("reason_for_admission", ReadField(value=match.group(1).strip(), page=page, confidence=TIER["narrative"], basis="narrative", text=match.group(0).strip()[:200])))
    return found


def _settle(found: list[ReadField]) -> ReadField:
    """One value per field: the most reliable reading, or a conflict if readings disagree."""
    best = max(f.confidence for f in found)
    top = [f for f in found if f.confidence == best and f.value not in (None, "")] or found[:1]
    distinct = list(dict.fromkeys(_normal(f.value) for f in top))
    if len(distinct) > 1:
        return ReadField(value=None, page=top[0].page, confidence=0.0, basis="conflict", conflict=[str(f.value) for f in top], text=top[0].text)
    return top[0]


def _normal(value: str | int | None) -> str:
    return re.sub(r"[\s.,;]+", " ", str(value)).strip().lower()


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


def _alternate(doc_type: DocumentType, text: str) -> list[tuple[str, str, Basis, str]]:
    """Fields under other labels ("Presenting complaint: ...") or headings ("IMPRESSION" then a paragraph)."""
    labels = {label: name for name, options in SYNONYMS.get(doc_type, {}).items() for label in options}
    lines = ["" if _NOTICE_LINE.search(line) else line.strip() for line in text.splitlines()]
    found: list[tuple[str, str, Basis, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _LABELLED.match(line)
        heading = line.rstrip(":").strip().lower()
        if match and match[1].strip().lower() in labels:
            name, value, basis = labels[match[1].strip().lower()], match[2].strip(), "alternate_label"
        elif heading in labels and (line.isupper() or line.endswith(":")) and i + 1 < len(lines):
            name, value, basis = labels[heading], "", "heading"
        else:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j] and not _starts_field(lines[j], labels) and lines[j] not in _STOP_LINES:
            # a labelled value only runs on when it was clearly cut mid-sentence; a heading takes its paragraph
            if basis == "alternate_label" and not (value.endswith(",") or lines[j][:1].islower()):
                break
            if basis == "heading" and len(value) > 0 and value.endswith("."):
                break
            value = f"{value} {lines[j]}".strip()
            j += 1
        if value:
            found.append((name, value, basis, line if basis == "alternate_label" else f"{line} {value}"))
        i = j
    return found


def _starts_field(line: str, labels: dict[str, str]) -> bool:
    if re.match(r"^[A-Za-z][A-Za-z0-9 /()'-]{0,40}?:\s", line + " "):
        return True
    return (line.isupper() and len(line) < 50) or line.lower().rstrip(":") in labels


def _bill_total(text: str) -> dict[str, int]:
    """The final bill's total: the amount on the line after "Total billed", or on a total line, e.g. "Rs. 2,40,000"."""
    lines = [line.strip() for line in text.splitlines()]
    for label, value in zip(lines, lines[1:]):
        if label == "Total billed" and (digits := re.sub(r"\D", "", value)):
            return {"total_billed": int(digits)}
    for line in lines:
        if match := re.match(r"^(grand total|total amount|net amount|total billed)\s*[:\-]?\s*(?:rs\.?|inr|₹)?\s*([\d,]+)", line, re.IGNORECASE):
            return {"total_billed": int(match[2].replace(",", ""))}
    return {}


def _key(label: str) -> str:
    """'Wbc per ul' -> 'wbc_per_ul', 'ICD-10' -> 'icd10'."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower().replace("-", "")).strip("_")
