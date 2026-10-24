"""Import a hospital billing export (CSV or JSON) as ClaimPilot cases (refinement of phase 10).

One row per bill line; the admission columns repeat on each of its lines:

    admission_ref, patient_ref, patient_name, age, gender, city, policy_config, sum_insured,
    policy_start, procedure, icd10, room_category, admit_date, discharge_date, tpa, emergency,
    category, description, quantity, amount

JSON is the same rows as a list of objects. The policy's rules come from a named policy
configuration (backend/policy_config.py), so a hospital adds no code to bring its claims in.

Every problem is reported with its row and what to fix; nothing is imported from a file with errors.
An import is idempotent: each case's id comes from a hash of its content, so importing the same
admission again is recognised and skipped. Imported cases carry no documents; the denial-risk check
lists what to attach. Synthetic data only.
"""

import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend import policy_config
from backend.schemas import Admission, BillCategory, BillLine, Case, Patient, Policy

def imports_dir() -> Path:
    return Path(os.environ.get("CLAIMPILOT_IMPORTS", "data/imports"))

COLUMNS = [
    "admission_ref", "patient_ref", "patient_name", "age", "gender", "city", "policy_config", "sum_insured", "policy_start",
    "procedure", "icd10", "room_category", "admit_date", "discharge_date", "tpa", "emergency",
    "category", "description", "quantity", "amount",
]
MAX_ROWS = 2_000


class ImportResult(dict):
    """{"imported": [case ids], "duplicates": [case ids], "errors": [{"row", "column", "problem", "fix"}]}"""


def parse(content: str, kind: str) -> list[dict[str, Any]]:
    if kind == "json":
        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError("The JSON should be a list of rows")
        return rows
    return list(csv.DictReader(io.StringIO(content)))


def import_rows(rows: list[dict[str, Any]], folder: Path | None = None) -> ImportResult:
    errors: list[dict[str, Any]] = []
    if not rows:
        return ImportResult(imported=[], duplicates=[], errors=[{"row": None, "column": None, "problem": "The file has no rows", "fix": "Export at least one bill line"}])
    if len(rows) > MAX_ROWS:
        return ImportResult(imported=[], duplicates=[], errors=[{"row": None, "column": None, "problem": f"{len(rows)} rows; the limit is {MAX_ROWS}", "fix": "Split the export"}])
    missing = [c for c in COLUMNS if c not in rows[0]]
    if missing:
        return ImportResult(imported=[], duplicates=[], errors=[{"row": None, "column": ", ".join(missing), "problem": "Columns missing", "fix": f"The header must include: {', '.join(COLUMNS)}"}])

    configs = policy_config.load_all()
    by_admission: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        by_admission[str(row["admission_ref"]).strip()].append((number, row))

    cases: list[Case] = []
    for ref, lines in by_admission.items():
        first_row, head = lines[0]
        problems = _check_head(first_row, head, configs)
        problems += [p for n, row in lines[1:] for p in _check_same(n, row, head)]
        bill = []
        for i, (number, row) in enumerate(lines, start=1):
            line, line_problems = _bill_line(number, row, ref, i)
            problems += line_problems
            if line:
                bill.append(line)
        if problems:
            errors += problems
            continue
        try:
            cases.append(_case(ref, head, bill, configs))
        except ValidationError as error:
            errors += [{"row": first_row, "column": ".".join(map(str, e["loc"])), "problem": e["msg"], "fix": "Correct this value in the export"} for e in error.errors()]
    if errors:
        return ImportResult(imported=[], duplicates=[], errors=errors)

    folder = folder or imports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    imported, duplicates = [], []
    for case in cases:
        path = folder / f"{case.case_id}.json"
        if path.exists():
            duplicates.append(case.case_id)
            continue
        path.write_text(case.model_dump_json(indent=2), encoding="utf-8")
        imported.append(case.case_id)
    return ImportResult(imported=imported, duplicates=duplicates, errors=[])


def imported_cases(folder: Path | None = None) -> list[Case]:
    folder = folder or imports_dir()
    return [Case.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def _error(row: int, column: str, problem: str, fix: str) -> dict[str, Any]:
    return {"row": row, "column": column, "problem": problem, "fix": fix}


def _check_head(number: int, row: dict, configs: dict) -> list[dict]:
    problems = []
    if str(row["policy_config"]).strip() not in configs:
        problems.append(_error(number, "policy_config", f"Unknown policy configuration '{row['policy_config']}'", f"Use one of: {', '.join(sorted(configs))}"))
    for column in ("age", "sum_insured"):
        if not str(row[column]).strip().isdigit():
            problems.append(_error(number, column, f"'{row[column]}' is not a whole number", "Write it as digits only, e.g. 300000"))
    for column in ("policy_start", "admit_date", "discharge_date"):
        try:
            date.fromisoformat(str(row[column]).strip())
        except ValueError:
            problems.append(_error(number, column, f"'{row[column]}' is not a date", "Use YYYY-MM-DD"))
    if str(row["tpa"]).strip() not in ("TPA-A", "TPA-B", "TPA-C"):
        problems.append(_error(number, "tpa", f"Unknown TPA '{row['tpa']}'", "Use TPA-A, TPA-B or TPA-C"))
    if str(row["gender"]).strip() not in ("M", "F", "O"):
        problems.append(_error(number, "gender", f"'{row['gender']}' is not M, F or O", "Use M, F or O"))
    return problems


def _check_same(number: int, row: dict, head: dict) -> list[dict]:
    """The admission columns must repeat unchanged on each of its lines."""
    return [
        _error(number, column, f"'{row[column]}' differs from the admission's first line ('{head[column]}')", "Make the admission columns the same on every line")
        for column in COLUMNS[:16] if str(row[column]).strip() != str(head[column]).strip()
    ]


def _bill_line(number: int, row: dict, ref: str, index: int) -> tuple[BillLine | None, list[dict]]:
    problems = []
    category = str(row["category"]).strip()
    if category not in {c.value for c in BillCategory}:
        problems.append(_error(number, "category", f"Unknown category '{category}'", f"Use one of: {', '.join(c.value for c in BillCategory)}"))
    amount, quantity = str(row["amount"]).replace(",", "").strip(), str(row.get("quantity") or "1").strip()
    if not amount.isdigit():
        problems.append(_error(number, "amount", f"'{row['amount']}' is not a whole rupee amount", "Digits only, e.g. 20000"))
    if not quantity.isdigit() or int(quantity) < 1:
        problems.append(_error(number, "quantity", f"'{row.get('quantity')}' is not a count of 1 or more", "Use days for room lines, else 1"))
    if not str(row["description"]).strip():
        problems.append(_error(number, "description", "The description is empty", "Describe the charge"))
    if problems:
        return None, problems
    return BillLine(line_id=f"{ref}-L{index}", category=category, description=str(row["description"]).strip(), quantity=int(quantity), amount=int(amount)), []


def _case(ref: str, row: dict, bill: list[BillLine], configs: dict) -> Case:
    content = json.dumps({"head": {c: str(row[c]).strip() for c in COLUMNS[:16]}, "bill": [b.model_dump(mode="json") for b in bill]}, sort_keys=True)
    digest = hashlib.sha256(content.encode()).hexdigest()[:10]
    admission_id, patient_id, policy_id = f"IMP-{ref}", f"IMP-{row['patient_ref']}".strip(), f"IMP-POL-{ref}"
    config = configs[str(row["policy_config"]).strip()]
    return Case(
        case_id=f"imp-{digest}",
        title=f"Imported admission {ref}: {str(row['procedure']).replace('_', ' ')}",
        patient=Patient(patient_id=patient_id, name=str(row["patient_name"]).strip(), age=int(row["age"]), gender=str(row["gender"]).strip(), city=str(row["city"]).strip()),
        policy=Policy(policy_id=policy_id, insurer=config.insurer, product=config.product, sum_insured=int(row["sum_insured"]),
                      start_date=date.fromisoformat(str(row["policy_start"]).strip()), rules=[r.model_copy(update={"rule_id": f"{policy_id}-{r.rule_id}"}) for r in config.rules]),
        admission=Admission(
            admission_id=admission_id, patient_id=patient_id, policy_id=policy_id, procedure=str(row["procedure"]).strip(), icd10=str(row["icd10"]).strip(),
            room_category=str(row["room_category"]).strip(), admit_date=date.fromisoformat(str(row["admit_date"]).strip()),
            discharge_date=date.fromisoformat(str(row["discharge_date"]).strip()), tpa_id=str(row["tpa"]).strip(),
            emergency=str(row["emergency"]).strip().lower() in ("1", "true", "yes", "y"),
        ),
        bill_lines=bill,
        documents=[],
    )
