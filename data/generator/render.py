"""Render documents to PDF, and make scanned-looking copies (design section 8).

Each document is one A4 page laid out from HTML with PyMuPDF. Every field value is printed from the
structured data, so the ground truth is exact. A scan is the page rasterised, tilted, blurred and
speckled, then saved as an image-only PDF, so only a vision reader or OCR can read it.
"""

import io
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageFilter

from backend.schemas import BillCategory, Case, Document, DocumentType

HOSPITAL = "ClaimPilot Demo Hospital"
HOSPITAL_ADDRESS = "14 Hospital Road, Bengaluru 560001"
SYNTHETIC_NOTICE = "Synthetic document generated for the ClaimPilot demo. Not a real patient record."

TITLES = {
    DocumentType.ADMISSION_NOTE: "Admission Note",
    DocumentType.DISCHARGE_SUMMARY: "Discharge Summary",
    DocumentType.OPERATIVE_NOTE: "Operative Note",
    DocumentType.XRAY_REPORT: "X-ray Report",
    DocumentType.LAB_REPORT: "Laboratory Report",
    DocumentType.PRESCRIPTION: "Prescription",
    DocumentType.FINAL_BILL: "Final Bill",
}
NARRATIVE_HEADINGS = {
    DocumentType.DISCHARGE_SUMMARY: "Hospital course",
    DocumentType.LAB_REPORT: "Remarks",
}
_DISCHARGE_DOCS = {DocumentType.DISCHARGE_SUMMARY, DocumentType.FINAL_BILL}

_A4 = fitz.paper_rect("a4")
_CSS = """
* { font-family: sans-serif; font-size: 10pt; color: #111; }
h1 { font-size: 15pt; margin: 0; }
h2 { font-size: 12pt; margin: 14pt 0 6pt 0; border-bottom: 1px solid #555; }
h3 { font-size: 10.5pt; margin: 12pt 0 4pt 0; }
.address, .notice { font-size: 8pt; color: #555; }
table { border-collapse: collapse; width: 100%; }
td, th { padding: 2pt 4pt; vertical-align: top; text-align: left; }
th { border-bottom: 1px solid #555; }
p.field { margin: 0 0 3pt 0; }
td.amount, th.amount { text-align: right; }
tr.total td { border-top: 1px solid #555; font-weight: bold; }
"""


def header_fields(doc: Document, case: Case) -> dict[str, str]:
    """The patient block printed at the top of every document."""
    patient, admission = case.patient, case.admission
    fields = {
        "patient_name": patient.name,
        "patient_id": patient.patient_id,
        "age": str(patient.age),
        "gender": patient.gender,
        "admission_id": admission.admission_id,
        "admit_date": admission.admit_date.isoformat(),
    }
    if doc.type in _DISCHARGE_DOCS:
        fields["discharge_date"] = admission.discharge_date.isoformat()
    return fields


def render_pdf(doc: Document, case: Case, narrative: str | None) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=_A4.width, height=_A4.height)
    spare, _ = page.insert_htmlbox(fitz.Rect(48, 42, _A4.width - 48, _A4.height - 42), _html(doc, case, narrative), css=_CSS)
    if spare < 0:
        raise ValueError(f"{doc.doc_id} does not fit on one page")
    return pdf.tobytes(garbage=3, deflate=True)


def make_scan(pdf_bytes: bytes, seed: int) -> bytes:
    """A scanned-looking, image-only copy of a one-page PDF."""
    rng = np.random.default_rng(seed)
    page = fitz.open("pdf", pdf_bytes)[0]
    pixmap = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    image = image.rotate(rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(0.7))

    pixels = np.asarray(image, dtype=float) * 0.88 + 18  # greyish paper, faded ink
    pixels += rng.normal(0, 7, pixels.shape)
    specks = rng.random(pixels.shape) < 0.001
    pixels[specks] = rng.uniform(40, 140, specks.sum())
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))

    jpeg = io.BytesIO()
    image.save(jpeg, "JPEG", quality=55)
    scan = fitz.open()
    scan_page = scan.new_page(width=page.rect.width, height=page.rect.height)
    scan_page.insert_image(scan_page.rect, stream=jpeg.getvalue())
    return scan.tobytes(deflate=True)


def pdf_text(path: str | Path) -> str:
    """Text of a PDF with ligatures expanded (the renderer sets "fi" as one glyph, like real PDFs)."""
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    return "".join(page.get_text(flags=flags) for page in fitz.open(path))


def inr(amount: int) -> str:
    """Indian digit grouping: 240000 -> 'Rs. 2,40,000'."""
    digits = str(amount)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])
    return f"Rs. {digits}"


def _html(doc: Document, case: Case, narrative: str | None) -> str:
    header = header_fields(doc, case)
    patient_rows = [
        ("Patient name", header["patient_name"]),
        ("Patient ID", header["patient_id"]),
        ("Age / Sex", f"{header['age']} / {header['gender']}"),
        ("Admission ID", header["admission_id"]),
        ("Admission date", header["admit_date"]),
    ]
    if "discharge_date" in header:
        patient_rows.append(("Discharge date", header["discharge_date"]))

    parts = [
        f"<h1>{HOSPITAL}</h1><p class='address'>{HOSPITAL_ADDRESS}</p>",
        f"<h2>{TITLES[doc.type]}</h2>",
        _table(patient_rows),
    ]
    if doc.type == DocumentType.FINAL_BILL:
        parts.append(_bill_table(case))
    else:
        rows = [(_label(key), str(value)) for key, value in doc.fields.items() if value is not None]
        parts += ["<h3>Details</h3>", _table(rows)]
    if narrative:
        parts.append(f"<h3>{NARRATIVE_HEADINGS[doc.type]}</h3><p>{escape(narrative)}</p>")
    parts.append(f"<p class='notice'>{SYNTHETIC_NOTICE}</p>")
    return "".join(parts)


def _table(rows: list[tuple[str, str]]) -> str:
    # Label: value lines, not an HTML table; MuPDF squeezes a two-column table's label column.
    return "".join(f"<p class='field'><b>{escape(k)}:</b> {escape(v)}</p>" for k, v in rows)


def _bill_table(case: Case) -> str:
    rows = "".join(
        f"<tr><td>{escape(line.description)}</td><td>{_label(line.category)}</td>"
        f"<td class='amount'>{line.quantity}</td><td class='amount'>{inr(line.amount)}</td></tr>"
        for line in case.bill_lines
    )
    total = sum(line.amount for line in case.bill_lines)
    return (
        "<h3>Charges</h3><table><tr><th>Item</th><th>Category</th><th class='amount'>Qty</th>"
        f"<th class='amount'>Amount</th></tr>{rows}"
        f"<tr class='total'><td>Total billed</td><td></td><td></td><td class='amount'>{inr(total)}</td></tr></table>"
    )


def _label(key: str) -> str:
    if key == BillCategory.OT_CHARGES:
        return "OT charges"
    return key.replace("_", " ").capitalize().replace("Icd10", "ICD-10")
