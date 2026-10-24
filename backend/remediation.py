"""Fixing what the checks found: attach a missing record, or record a correction, then recheck (refinement of phase 7).

Detecting a problem isn't enough; the billing officer has to be able to resolve it. This module does
the two hospital-side fixes and keeps their provenance:

- `attach`: stores an uploaded synthetic PDF under the uploads folder, named by its content hash,
  and adds it to the case file. Only PDFs with a text layer or images, of an allowed document type,
  up to MAX_BYTES and MAX_PAGES, are accepted. The same file uploaded twice is recognised and not
  added again.
- `correct`: records a person's explicit correction of one extracted field, with who and why. The
  original document is never changed; the correction is applied on top of what the reader read.

Both are written to the append-only audit log. The orchestrator's `recheck` then re-reads the
documents, reruns the dependent checks, and drafts a new version that needs a fresh approval.
"""

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import fitz

from backend.db import audit
from backend.db.cases import save_case
from backend.db.db import connect
from backend.schemas import Case, Document, DocumentType

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 20
ACTOR = "remediation"


class UploadRejected(ValueError):
    """The file can't be accepted; the message says what to fix."""


def uploads_dir() -> Path:
    return Path(os.environ.get("CLAIMPILOT_UPLOADS", "data/uploads"))


def attach(
    db_path: Path | str, claim_id: str, case: Case, doc_type: DocumentType, filename: str, content: bytes, officer: str
) -> tuple[Case, Document, bool]:
    """Add an uploaded document to the case. Returns (case, document, duplicate)."""
    if len(content) > MAX_BYTES:
        raise UploadRejected(f"The file is {len(content):,} bytes; the limit is {MAX_BYTES:,}. Upload a smaller PDF.")
    if not content.startswith(b"%PDF-"):
        raise UploadRejected("Only PDF files are accepted. Save or scan the record as a PDF and upload it again.")
    try:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            pages = pdf.page_count
    except Exception:
        raise UploadRejected("The PDF could not be opened; it may be damaged. Export it again and re-upload.")
    if not 1 <= pages <= MAX_PAGES:
        raise UploadRejected(f"The PDF has {pages} pages; between 1 and {MAX_PAGES} are accepted.")

    sha256 = hashlib.sha256(content).hexdigest()
    earlier = {e["output"]["sha256"]: e["output"]["doc_id"] for e in audit.events(db_path, claim_id) if e["action"] == "document_attached"}
    if sha256 in earlier and (existing := next((d for d in case.documents if d.doc_id == earlier[sha256]), None)):
        audit.log(db_path, claim_id, f"human:{officer}", "document_duplicate", {"doc_id": existing.doc_id, "sha256": sha256})
        return case, existing, True

    folder = uploads_dir() / re.sub(r"[^A-Za-z0-9-]", "_", claim_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256[:16]}.pdf"
    path.write_bytes(content)
    count = 1 + sum(1 for d in case.documents if d.doc_id.startswith(f"{case.admission.admission_id}-U"))
    doc = Document(doc_id=f"{case.admission.admission_id}-U{count}", type=doc_type, file_path=path.as_posix())
    case = case.model_copy(update={"documents": [*case.documents, doc]})
    conn = connect(db_path)
    try:
        save_case(conn, case)
    finally:
        conn.close()
    audit.log(db_path, claim_id, f"human:{officer}", "document_attached", {
        "doc_id": doc.doc_id, "type": doc_type, "sha256": sha256, "bytes": len(content), "pages": pages,
        "filename": Path(filename).name[:120], "file_path": doc.file_path,
    })
    return case, doc, False


def correct(db_path: Path | str, claim_id: str, case: Case, doc_id: str, field: str, value: str, officer: str, reason: str) -> dict[str, Any]:
    """Record a person's correction of one field the reader extracted."""
    if not any(d.doc_id == doc_id for d in case.documents):
        raise UploadRejected(f"{doc_id} is not a document in this claim")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", field):
        raise UploadRejected("The field name should look like 'reason_for_admission'")
    record = {"doc_id": doc_id, "field": field, "value": value.strip(), "reason": reason.strip(), "officer": officer}
    audit.log(db_path, claim_id, f"human:{officer}", "field_correction", record)
    return record


def corrections(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    return [e["output"] for e in audit.events(db_path, claim_id) if e["action"] == "field_correction"]


def attachments(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """Where each attached document came from: who, when, its hash."""
    return [
        {**e["output"], "by": e["actor"].removeprefix("human:"), "at": e["created_at"]}
        for e in audit.events(db_path, claim_id) if e["action"] == "document_attached"
    ]


def tasks(latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What a person can do to resolve the open findings, from each agent's latest output."""
    out: list[dict[str, Any]] = []
    if risk := latest.get("denial_risk"):
        for item in risk["data"]["items"]:
            if item["flag"].startswith("missing_"):
                out.append({"kind": "attach", "doc_type": item["flag"].removeprefix("missing_"), "issue": item["issue"], "fix": item["fix"], "source": item["source"]})
            elif item["flag"] == "admission_reason_missing":
                out.append({"kind": "attach_or_correct", "doc_type": "discharge_summary", "issue": item["issue"], "fix": item["fix"], "source": item["source"]})
    if reader := latest.get("document_reader"):
        for doc in reader["data"]["documents"]:
            for name, field in doc["fields"].items():
                if field.get("basis") == "conflict":
                    out.append({"kind": "correct", "doc_id": doc["doc_id"], "field": name, "issue": f"Conflicting {name.replace('_', ' ')}", "fix": "Pick the right value and record why", "source": f"{doc['doc_id']} p.{field['page']}"})
            if doc.get("unreadable_pages"):
                out.append({"kind": "read_scan", "doc_id": doc["doc_id"], "issue": "Scanned page can't be read", "fix": "Read it and record the fields, or attach a text copy", "source": doc["doc_id"]})
            if doc.get("instructions"):
                out.append({"kind": "review_document", "doc_id": doc["doc_id"], "issue": "Instruction found in a document", "fix": "Check where the document came from before using it", "source": doc["doc_id"]})
    if (appeal := latest.get("appeal")) and appeal["data"].get("held_back") and not appeal["data"].get("appeal"):
        for held in appeal["data"]["held_back"]:
            out.append({"kind": "find_evidence", "issue": f"Not appealed: {held['clause']}", "fix": "Attach a record that shows why, or accept the decision", "source": held["why"]})
    return out
