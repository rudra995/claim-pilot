"""Preset patients for the admit screen: a varied handful of synthetic admissions, written as fixtures.

    python -m data.generator.presets

Most are clean claims; a few carry the everyday problems hospitals see (a room above the cap, a
sub-limit, non-payable items, a missing document, an unserved waiting period). They are picked by
admission number from the seeded generator, so the same patients come out on every machine. Ids are
re-prefixed PRE- so they never clash with the generator's SYN- rows in the database.
Synthetic data only.
"""

import json
from pathlib import Path

from backend.schemas import Case
from data.generator.generate import generate
from data.generator.procedures import BY_SLUG
from data.generator.render import render_pdf

OUT_DIR = Path("fixtures/patients")
DOCS_DIR = OUT_DIR / "documents"

# Admission numbers from the seeded generator: eight clean claims, then one of each common problem.
PICKS = [
    "SYN-ADM-0004",  # CABG, clean
    "SYN-ADM-0009",  # caesarean section, clean
    "SYN-ADM-0024",  # tonsillectomy, clean
    "SYN-ADM-0038",  # femur fracture fixation, clean
    "SYN-ADM-0040",  # lithotripsy, clean
    "SYN-ADM-0060",  # coronary angioplasty, clean
    "SYN-ADM-0069",  # spinal fusion, clean
    "SYN-ADM-0073",  # laparoscopic cholecystectomy, clean
    "SYN-ADM-0017",  # caesarean section, room above the cap
    "SYN-ADM-0054",  # hernia repair, above the procedure's sub-limit
    "SYN-ADM-0031",  # TURP, non-payable items billed
    "SYN-ADM-0045",  # TURP, a required document missing
    "SYN-ADM-0064",  # cataract surgery, waiting period not served
]


def preset_cases() -> list[Case]:
    by_id = {g.case.admission.admission_id: g.case for g, _ in generate()}
    cases = []
    for admission_id in PICKS:
        case = by_id[admission_id]
        data = json.loads(case.model_dump_json().replace("SYN-", "PRE-"))
        number = admission_id.rsplit("-", 1)[1]
        data["case_id"] = f"p{number}"
        data["title"] = BY_SLUG[case.admission.procedure].diagnosis
        folder = DOCS_DIR / data["admission"]["admission_id"]
        for doc in data["documents"]:
            doc["file_path"] = (folder / f"{doc['doc_id']}.pdf").as_posix()
        cases.append(Case.model_validate(data))
    return cases


def main() -> None:
    for case in preset_cases():
        for doc in case.documents:
            path = Path(doc.file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(render_pdf(doc, case, None))
        (OUT_DIR / f"{case.case_id}.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")
        print(f"{case.case_id}  {case.patient.name:<20} {case.title:<30} {case.admission.tpa_id}")


if __name__ == "__main__":
    main()
