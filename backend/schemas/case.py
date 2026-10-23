"""A full case as loaded from fixtures: patient, policy (with its rules), admission, bill, documents.

Mirrors the tables in backend/db/schema.sql. Amounts are whole rupees. Synthetic data only.
"""

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.agent_output import NonEmptyStr
from backend.schemas.bill import BillCategory
from backend.schemas.rules import Rule


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentType(StrEnum):
    ADMISSION_NOTE = "admission_note"
    DISCHARGE_SUMMARY = "discharge_summary"
    OPERATIVE_NOTE = "operative_note"
    XRAY_REPORT = "xray_report"
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    FINAL_BILL = "final_bill"


class Patient(_Model):
    patient_id: NonEmptyStr
    name: NonEmptyStr
    age: int = Field(ge=0, le=120)
    gender: Literal["M", "F", "O"]
    city: NonEmptyStr


class Policy(_Model):
    policy_id: NonEmptyStr
    insurer: NonEmptyStr
    product: NonEmptyStr
    sum_insured: int = Field(gt=0)
    start_date: date
    rules: list[Rule] = Field(default_factory=list)


class Admission(_Model):
    admission_id: NonEmptyStr
    patient_id: NonEmptyStr
    policy_id: NonEmptyStr
    procedure: NonEmptyStr
    icd10: NonEmptyStr
    room_category: NonEmptyStr
    admit_date: date
    discharge_date: date
    tpa_id: Literal["TPA-A", "TPA-B", "TPA-C"]
    emergency: bool = False

    @model_validator(mode="after")
    def _dates_in_order(self) -> "Admission":
        if self.discharge_date < self.admit_date:
            raise ValueError("discharge_date is before admit_date")
        return self


class BillLine(_Model):
    line_id: NonEmptyStr
    category: BillCategory
    description: NonEmptyStr
    quantity: int = Field(default=1, ge=1)  # days, for room and ICU lines
    amount: int = Field(ge=0)  # line total


class Document(_Model):
    doc_id: NonEmptyStr
    type: DocumentType
    file_path: str | None = None  # filled when documents are rendered (phase 3)
    fields: dict[str, Any] = Field(default_factory=dict)  # true values; the reader's ground truth


class Case(_Model):
    case_id: NonEmptyStr
    title: NonEmptyStr
    patient: Patient
    policy: Policy
    admission: Admission
    bill_lines: list[BillLine] = Field(min_length=1)
    documents: list[Document] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_consistent(self) -> "Case":
        if self.admission.patient_id != self.patient.patient_id:
            raise ValueError("admission.patient_id does not match patient")
        if self.admission.policy_id != self.policy.policy_id:
            raise ValueError("admission.policy_id does not match policy")
        for label, ids in (
            ("bill line", [line.line_id for line in self.bill_lines]),
            ("document", [doc.doc_id for doc in self.documents]),
            ("rule", [rule.rule_id for rule in self.policy.rules]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")
        return self
