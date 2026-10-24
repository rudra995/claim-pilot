"""Extra demo cases built from the scripted ones (refinement of phase 14).

    python -m data.demo_cases

case4: Case 3's gastroenteritis stay, but the admission records show nothing that needed a hospital
bed (no abnormal vitals or labs). TPA-B still cuts the stay at discharge; this time the appeal agent
holds back instead of arguing, and a person decides. It shows an unsupported appeal being stopped.
Synthetic data only.
"""

import json
from pathlib import Path

from backend.schemas import Case
from data.generator.render import render_pdf

OUT = Path("fixtures/demo")


def case4() -> Case:
    base = Case.model_validate_json(Path("fixtures/case3.json").read_text(encoding="utf-8"))
    text = base.model_dump_json().replace("ADM-0003", "ADM-0004").replace("PAT-0003", "PAT-0004").replace("POL-B-0003", "POL-B-0004")
    data = json.loads(text)
    data |= {"case_id": "case4", "title": "Gastroenteritis stay: records don't show why admission was needed"}
    data["patient"]["name"] = "Suresh Nair"
    fields = {
        "admission_note": {"chief_complaint": "Loose stools for two days, eating and drinking normally", "provisional_diagnosis": "Acute gastroenteritis", "planned_treatment": "Observation"},
        "lab_report": {"test": "Serum electrolytes and renal function", "finding": "Within normal limits"},
    }
    for doc in data["documents"]:
        doc["fields"] = fields.get(doc["type"], doc["fields"])
        doc["file_path"] = f"{OUT.as_posix()}/documents/{doc['doc_id']}.pdf"
    return Case.model_validate(data)


def main() -> None:
    case = case4()
    (OUT / "documents").mkdir(parents=True, exist_ok=True)
    for doc in case.documents:
        Path(doc.file_path).write_bytes(render_pdf(doc, case, None))
    (OUT / "case4.json").write_text(case.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT / 'case4.json'} and {len(case.documents)} documents")


if __name__ == "__main__":
    main()
