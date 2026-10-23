"""Features for the cost estimator and the denial-risk model (design section 7).

Both are built from a Case alone, using only what is known before the claim is sent, so the same
code makes the training rows and scores a live claim. Gender is never a feature (design section 9).
Categories are fixed lists, so an unseen value (a procedure outside the 25) becomes missing, not a crash.
"""

import math
from datetime import date

import pandas as pd

from backend.rules.engine import assess
from backend.schemas import Case, DocumentType, RuleType
from data.generator.admissions import ADMISSION_REASON, ROOMS, required_documents
from data.generator.generate import generate
from data.generator.patients import CITIES
from data.generator.procedures import BY_SLUG

CATEGORIES = {
    "procedure": sorted(BY_SLUG),
    "room_category": [slug for slug, _, _ in ROOMS],
    "city": sorted(CITIES),
    "tpa_id": ["TPA-A", "TPA-B", "TPA-C"],
}

COST_FEATURES = ["procedure", "room_category", "city", "age", "emergency"]

# Documents a claim needs; a missing one is a flag.
DOC_FLAGS = {
    f"missing_{t.value}": t
    for t in (
        DocumentType.ADMISSION_NOTE,
        DocumentType.LAB_REPORT,
        DocumentType.XRAY_REPORT,
        DocumentType.OPERATIVE_NOTE,
        DocumentType.DISCHARGE_SUMMARY,
    )
}
# Problems the rules engine finds in the bill.
RULE_FLAGS = {
    "room_over_cap": RuleType.ROOM_RENT_CAP,
    "non_payables_billed": RuleType.NON_PAYABLE,
    "sub_limit_exceeded": RuleType.SUB_LIMIT,
    "waiting_period_breached": RuleType.WAITING_PERIOD,
    "excluded_procedure": RuleType.EXCLUSION,
}
# Every flag a person can act on before sending; fixes_map.py has a fix for each.
FLAGS = [*DOC_FLAGS, "admission_reason_missing", *RULE_FLAGS, "coding_mismatch"]
DENIAL_FEATURES = ["tpa_id", "procedure", "age", "emergency", "log_billed", *FLAGS]


def cost_features(case: Case) -> dict[str, object]:
    return {
        "procedure": case.admission.procedure,
        "room_category": case.admission.room_category,
        "city": case.patient.city,
        "age": case.patient.age,
        "emergency": int(case.admission.emergency),
    }


def denial_features(case: Case) -> dict[str, object]:
    procedure = BY_SLUG.get(case.admission.procedure)
    required = set(
        required_documents(procedure)
        if procedure
        else [DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT, DocumentType.DISCHARGE_SUMMARY]
    )
    present = {doc.type for doc in case.documents}
    summary = next((d for d in case.documents if d.type == DocumentType.DISCHARGE_SUMMARY), None)

    rule_types = {rule.rule_id: rule.type for rule in case.policy.rules}
    cut_by = {rule_types.get(d.rule_id) for d in assess(case).deductions}

    billed = sum(line.amount for line in case.bill_lines)
    return {
        "tpa_id": case.admission.tpa_id,
        "procedure": case.admission.procedure,
        "age": case.patient.age,
        "emergency": int(case.admission.emergency),
        "log_billed": math.log(max(billed, 1)),
        **{flag: int(t in required and t not in present) for flag, t in DOC_FLAGS.items()},
        "admission_reason_missing": int(summary is not None and not summary.fields.get(ADMISSION_REASON)),
        **{flag: int(t in cut_by) for flag, t in RULE_FLAGS.items()},
        "coding_mismatch": int(procedure is not None and case.admission.icd10 != procedure.icd10),
    }


def frame(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    """Rows as a DataFrame with fixed categories, ready for XGBoost."""
    df = pd.DataFrame(rows, columns=columns)
    for column, categories in CATEGORIES.items():
        if column in df:
            known = df[column].where(df[column].isin(categories))
            df[column] = pd.Categorical(known, categories=categories)
    return df


def training_data() -> pd.DataFrame:
    """One row per generated admission: both feature sets, targets, outcome and admit date."""
    rows = []
    for generated, label in generate():
        case = generated.case
        rows.append({
            **cost_features(case),
            **denial_features(case),
            "admit_date": case.admission.admit_date,
            "billed": label.billed,
            "los": (case.admission.discharge_date - case.admission.admit_date).days,
            "outcome": label.outcome.value,
        })
    return frame(rows, list(rows[0]))


def time_split(df: pd.DataFrame, test_share: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame, date]:
    """Train on earlier admissions, test on the latest ones, so the test looks like the future."""
    df = df.sort_values("admit_date", kind="stable").reset_index(drop=True)
    cut = int(len(df) * (1 - test_share))
    return df.iloc[:cut], df.iloc[cut:], df["admit_date"].iloc[cut]
