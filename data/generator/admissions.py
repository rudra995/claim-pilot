"""Synthetic admissions, bills and document lists, with planted problems (design section 8).

Each admission gets exactly one planted problem, or none. After drafting, `detect_problems` runs
the real rules engine and the document checklist over the draft; a draft whose problems don't
match the plan is thrown away and redrawn. So the planted labels are exact, never accidental.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from backend.rules.engine import assess
from backend.schemas import Admission, BillCategory, BillLine, Case, Document, DocumentType, RuleType
from data.generator.patients import make_patient
from data.generator.policies import (
    PRODUCTS,
    ROOM_CAP_PERCENT,
    SUB_LIMITS,
    SUM_INSURED_OPTIONS,
    WAITING_PERIOD_PROCEDURES,
    make_policy,
)
from data.generator.procedures import BY_SLUG, PROCEDURES, Procedure

ID_PREFIX = "SYN-"  # every generated row's id starts with this, so a rerun replaces only its own rows
FIRST_ADMIT = date(2024, 1, 1)
LAST_ADMIT = date(2026, 9, 30)
MAX_ATTEMPTS = 200

# A discharge summary that doesn't say why admission was needed counts as a documentation gap.
ADMISSION_REASON = "reason_for_admission"

# Gaps planted for MISSING_DOCUMENT, with weights. The X-ray report and the admission reason are what
# TPA-A and TPA-B react to, so they get enough cases for those habits to show in the data.
_PLANTED_GAPS = {
    DocumentType.XRAY_REPORT.value: 3,
    ADMISSION_REASON: 3,
    DocumentType.ADMISSION_NOTE.value: 1,
    DocumentType.LAB_REPORT.value: 1,
    DocumentType.OPERATIVE_NOTE.value: 1,
    DocumentType.DISCHARGE_SUMMARY.value: 1,
}


class Problem(StrEnum):
    CLEAN = "clean"
    ROOM_OVER_CAP = "room_over_cap"
    MISSING_DOCUMENT = "missing_document"
    NON_PAYABLES = "non_payables"
    SUB_LIMIT_EXCEEDED = "sub_limit_exceeded"
    WAITING_PERIOD_BREACHED = "waiting_period_breached"
    CODING_MISMATCH = "coding_mismatch"


SHARES = {
    Problem.CLEAN: 0.45,
    Problem.ROOM_OVER_CAP: 0.15,
    Problem.MISSING_DOCUMENT: 0.12,
    Problem.NON_PAYABLES: 0.10,
    Problem.SUB_LIMIT_EXCEEDED: 0.08,
    Problem.WAITING_PERIOD_BREACHED: 0.06,
    Problem.CODING_MISMATCH: 0.04,
}

_PROBLEM_BY_RULE = {
    RuleType.ROOM_RENT_CAP: Problem.ROOM_OVER_CAP,
    RuleType.NON_PAYABLE: Problem.NON_PAYABLES,
    RuleType.SUB_LIMIT: Problem.SUB_LIMIT_EXCEEDED,
    RuleType.WAITING_PERIOD: Problem.WAITING_PERIOD_BREACHED,
    RuleType.CO_PAY: None,  # a co-pay is an expected share, not a problem
}

ROOMS = [  # slug, label, rate per day
    ("general_ward", "General ward", 1_500),
    ("twin_sharing", "Twin sharing room", 2_500),
    ("single_standard", "Single standard room", 3_000),
    ("single_ac", "Single AC room", 4_000),
    ("single_deluxe", "Single deluxe room", 5_500),
    ("suite", "Suite", 8_000),
]

# category, description, weight in the procedure's core cost
_LINE_TEMPLATES = {
    "surgical": [
        (BillCategory.DOCTOR_FEES, "Surgeon fee", 22),
        (BillCategory.DOCTOR_FEES, "Anaesthetist fee", 8),
        (BillCategory.DOCTOR_FEES, "Resident doctor visits", 5),
        (BillCategory.OT_CHARGES, "Operation theatre charges", 20),
        (BillCategory.OT_CHARGES, "Recovery room charges", 5),
        (BillCategory.NURSING, "Nursing charges", 8),
        (BillCategory.PHARMACY, "Medicines", 9),
        (BillCategory.PHARMACY, "IV fluids", 3),
        (BillCategory.PHARMACY, "Antibiotics", 5),
        (BillCategory.INVESTIGATIONS, "Blood tests", 5),
        (BillCategory.INVESTIGATIONS, "Imaging", 4),
        (BillCategory.INVESTIGATIONS, "ECG", 1),
        (BillCategory.OTHER, "Diet charges", 3),
        (BillCategory.OTHER, "Registration and medical records", 2),
    ],
    "medical": [
        (BillCategory.DOCTOR_FEES, "Consultant physician visits", 15),
        (BillCategory.DOCTOR_FEES, "Specialist consultation", 6),
        (BillCategory.DOCTOR_FEES, "Resident doctor visits", 4),
        (BillCategory.NURSING, "Nursing charges", 15),
        (BillCategory.PHARMACY, "Medicines", 18),
        (BillCategory.PHARMACY, "IV fluids", 8),
        (BillCategory.PHARMACY, "Antibiotics", 9),
        (BillCategory.INVESTIGATIONS, "Blood tests", 11),
        (BillCategory.INVESTIGATIONS, "Imaging", 6),
        (BillCategory.INVESTIGATIONS, "ECG", 3),
        (BillCategory.OTHER, "Diet charges", 3),
        (BillCategory.OTHER, "Registration and medical records", 2),
    ],
}
_CONSUMABLES = [("Gloves and masks", 1_500, 4_000), ("Admission kit", 800, 2_000)]


@dataclass(frozen=True)
class Generated:
    case: Case
    problem: Problem
    detail: str | None  # which document is missing, for MISSING_DOCUMENT


def make_admission(rng: random.Random, n: int, problem: Problem) -> Generated:
    expected = set() if problem == Problem.CLEAN else {problem}
    for _ in range(MAX_ATTEMPTS):
        draft = _draft(rng, n, problem)
        if draft is not None and detect_problems(draft.case) == expected:
            return draft
    raise RuntimeError(f"could not generate admission {n} with problem {problem}")


def required_documents(procedure: Procedure) -> list[DocumentType]:
    docs = [DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT]
    if procedure.ortho:
        docs.append(DocumentType.XRAY_REPORT)
    if procedure.kind == "surgical":
        docs.append(DocumentType.OPERATIVE_NOTE)
    return [*docs, DocumentType.DISCHARGE_SUMMARY, DocumentType.FINAL_BILL]


def documentation_gaps(case: Case) -> list[str]:
    """Required documents that are missing, plus ADMISSION_REASON if the summary leaves it out."""
    present = {doc.type: doc for doc in case.documents}
    gaps: list[str] = [
        doc_type.value
        for doc_type in required_documents(BY_SLUG[case.admission.procedure])
        if doc_type not in present
    ]
    summary = present.get(DocumentType.DISCHARGE_SUMMARY)
    if summary is not None and not summary.fields.get(ADMISSION_REASON):
        gaps.append(ADMISSION_REASON)
    return gaps


def detect_problems(case: Case) -> set[str]:
    """Every problem the rules engine and document checklist find in a case."""
    rules = case.policy.rules
    rule_types = {rule.rule_id: rule.type for rule in rules}
    # Waiting periods and exclusions zero the whole claim, hiding other cuts; check without them too.
    whole_claim = {RuleType.WAITING_PERIOD, RuleType.EXCLUSION}
    unmasked = case.model_copy(
        update={"policy": case.policy.model_copy(
            update={"rules": [rule for rule in rules if rule.type not in whole_claim]}
        )}
    )
    found: set[str] = set()
    for deduction in assess(case).deductions + assess(unmasked).deductions:
        rule_type = rule_types.get(deduction.rule_id)
        problem = _PROBLEM_BY_RULE.get(rule_type, f"unplanned:{rule_type or deduction.rule_id}")  # type: ignore[arg-type]
        if problem is not None:
            found.add(problem)
    if documentation_gaps(case):
        found.add(Problem.MISSING_DOCUMENT)
    if case.admission.icd10 != BY_SLUG[case.admission.procedure].icd10:
        found.add(Problem.CODING_MISMATCH)
    return found


def _draft(rng: random.Random, n: int, problem: Problem) -> Generated | None:
    missing = None
    if problem == Problem.MISSING_DOCUMENT:
        missing = rng.choices(list(_PLANTED_GAPS), weights=list(_PLANTED_GAPS.values()))[0]
    procedure = _pick_procedure(rng, problem, missing)
    patient_id, policy_id, admission_id = (f"{ID_PREFIX}{kind}-{n:04d}" for kind in ("PAT", "POL", "ADM"))
    patient = make_patient(rng, patient_id, procedure)
    product = rng.choice(PRODUCTS)
    admit = FIRST_ADMIT + timedelta(days=rng.randint(0, (LAST_ADMIT - FIRST_ADMIT).days))
    icu_days = rng.randint(*procedure.icu_days) if procedure.icu_days else 0
    room_days = max(1, rng.randint(*procedure.los) - icu_days)
    room_slug, room_label, room_rate = rng.choice(ROOMS)

    lines: list[tuple[BillCategory, str, int, int]] = [  # category, description, quantity, amount
        (BillCategory.ROOM, f"{room_label}, {room_days} days x {room_rate:,}", room_days, room_rate * room_days)
    ]
    if icu_days:
        icu_rate = rng.randrange(8_000, 15_001, 500)
        lines.append((BillCategory.ICU, f"ICU, {icu_days} days x {icu_rate:,}", icu_days, icu_rate * icu_days))
    if procedure.implant:
        name, low, high = procedure.implant
        lines.append((BillCategory.IMPLANTS, name, 1, _round10(rng.randint(low, high))))

    core = rng.randint(*procedure.core)
    if problem == Problem.SUB_LIMIT_EXCEEDED:
        target = round(SUB_LIMITS[procedure.slug] * rng.uniform(1.15, 1.5))
        core = target - sum(amount for *_, amount in lines)
        if core <= 0:
            return None
    lines += _core_lines(rng, procedure, core)
    if problem == Problem.NON_PAYABLES:
        for description, low, high in rng.sample(_CONSUMABLES, rng.randint(1, 2)):
            lines.append((BillCategory.CONSUMABLES, description, 1, _round10(rng.randint(low, high))))
    billed = sum(amount for *_, amount in lines)

    # Keep the sum insured comfortably above the bill, and put the room over or under the cap as planned.
    over_cap = problem == Problem.ROOM_OVER_CAP
    sum_insured_options = [
        si for si in SUM_INSURED_OPTIONS
        if si * 10 >= billed * 11 and (si * ROOM_CAP_PERCENT / 100 < room_rate) == over_cap
    ]
    if not sum_insured_options:
        return None
    sum_insured = rng.choice(sum_insured_options)

    covered_months = rng.randint(3, 20) if problem == Problem.WAITING_PERIOD_BREACHED else rng.randint(36, 96)
    icd10 = procedure.icd10
    if problem == Problem.CODING_MISMATCH:
        icd10 = rng.choice(sorted({p.icd10 for p in PROCEDURES} - {procedure.icd10}))

    case = Case(
        case_id=admission_id,
        title=f"Synthetic admission: {procedure.diagnosis}",
        patient=patient,
        policy=make_policy(policy_id, product, sum_insured, _months_before(admit, covered_months)),
        admission=Admission(
            admission_id=admission_id,
            patient_id=patient_id,
            policy_id=policy_id,
            procedure=procedure.slug,
            icd10=icd10,
            room_category=room_slug,
            admit_date=admit,
            discharge_date=admit + timedelta(days=room_days + icu_days),
            tpa_id=rng.choice(["TPA-A", "TPA-B", "TPA-C"]),
            emergency=procedure.emergency,
        ),
        bill_lines=[
            BillLine(line_id=f"{admission_id}-L{i}", category=category, description=description,
                     quantity=quantity, amount=amount)
            for i, (category, description, quantity, amount) in enumerate(lines, start=1)
        ],
        documents=_documents(admission_id, procedure, icd10, room_days + icu_days, billed, missing),
    )
    return Generated(case=case, problem=problem, detail=missing)


def _pick_procedure(rng: random.Random, problem: Problem, missing: str | None) -> Procedure:
    if problem == Problem.SUB_LIMIT_EXCEEDED:
        return BY_SLUG[rng.choice(sorted(SUB_LIMITS))]
    if problem == Problem.WAITING_PERIOD_BREACHED:
        return BY_SLUG[rng.choice(WAITING_PERIOD_PROCEDURES)]
    if missing is not None and missing != ADMISSION_REASON:
        return rng.choice([p for p in PROCEDURES if missing in {d.value for d in required_documents(p)}])
    return rng.choice(PROCEDURES)


def _core_lines(
    rng: random.Random, procedure: Procedure, core: int
) -> list[tuple[BillCategory, str, int, int]]:
    templates = _LINE_TEMPLATES[procedure.kind]
    weights = [weight * rng.uniform(0.8, 1.2) for *_, weight in templates]
    total = sum(weights)
    return [
        (category, description, 1, max(10, _round10(core * weight / total)))
        for (category, description, _), weight in zip(templates, weights)
    ]


def _documents(
    admission_id: str, procedure: Procedure, icd10: str, los: int, billed: int, missing: str | None
) -> list[Document]:
    fields: dict[DocumentType, dict] = {
        DocumentType.ADMISSION_NOTE: {
            "chief_complaint": procedure.complaint,
            "provisional_diagnosis": procedure.diagnosis,
            "planned_treatment": procedure.treatment,
        },
        DocumentType.LAB_REPORT: {"finding": procedure.lab_finding},
        DocumentType.XRAY_REPORT: {"finding": procedure.xray_finding},
        DocumentType.OPERATIVE_NOTE: {
            "procedure": procedure.treatment,
            "implant": procedure.implant[0] if procedure.implant else None,
        },
        DocumentType.DISCHARGE_SUMMARY: {
            "diagnosis": procedure.diagnosis,
            "icd10": icd10,
            ADMISSION_REASON: None if missing == ADMISSION_REASON else procedure.reason,
            "treatment_given": procedure.treatment,
            "length_of_stay_days": los,
            "condition_at_discharge": "Stable",
        },
        DocumentType.FINAL_BILL: {"total_billed": billed},
    }
    present = [d for d in required_documents(procedure) if d.value != missing]
    return [
        Document(doc_id=f"{admission_id}-D{i}", type=doc_type, fields=fields[doc_type])
        for i, doc_type in enumerate(present, start=1)
    ]


def _months_before(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 - months
    return date(month_index // 12, month_index % 12 + 1, min(day.day, 28))


def _round10(amount: float) -> int:
    return int(amount / 10 + 0.5) * 10
