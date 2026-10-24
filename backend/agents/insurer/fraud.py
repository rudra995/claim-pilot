"""TPA fraud screen: upcoding, unusually long stays and duplicate claims (design section 5).

Its flags go only to the insurer's adjudicator and to staff, never into a patient message.
"""

from typing import Literal

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.schemas import AgentOutput, Case, Finding
from data.generator.procedures import BY_SLUG

AGENT = "tpa_fraud_screen"
SOURCE = "Fraud screen rules"
LONG_STAY_FACTOR = 1.5  # a stay this many times the procedure's usual maximum is unusual


class FraudFlag(BaseModel):
    kind: Literal["upcoding", "long_stay", "duplicate"]
    detail: str


class FraudScreen(BaseModel):
    flags: list[FraudFlag]

    def of(self, kind: str) -> list[FraudFlag]:
        return [flag for flag in self.flags if flag.kind == kind]


def run(claim_id: str, case: Case, approved_claims: list[str]) -> AgentResult:
    """`approved_claims` are other claims on the same admission the insurer has already approved."""
    admission = case.admission
    procedure = BY_SLUG.get(admission.procedure)
    stay = (admission.discharge_date - admission.admit_date).days
    flags = []
    if procedure and admission.icd10 != procedure.icd10:
        flags.append(FraudFlag(kind="upcoding", detail=f"Coded {admission.icd10}, but {procedure.diagnosis.lower()} is {procedure.icd10}"))
    if procedure and stay > procedure.los[1] * LONG_STAY_FACTOR:
        flags.append(FraudFlag(kind="long_stay", detail=f"{stay}-day stay; {admission.procedure.replace('_', ' ')} usually needs up to {procedure.los[1]}"))
    if approved_claims:
        flags.append(FraudFlag(kind="duplicate", detail=f"Admission {admission.admission_id} was already approved on {', '.join(approved_claims)}"))

    findings = [Finding(issue=flag.kind.replace("_", " ").capitalize(), evidence=flag.detail, source=SOURCE) for flag in flags]
    if not flags:
        findings.append(Finding(issue="No fraud signals", evidence="Coding, length of stay and duplicates checked", source=SOURCE))
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="flagged" if flags else "clear",
            confidence=1.0,
            findings=findings,
            next="tpa_adjudicator",
        ),
        data=FraudScreen(flags=flags),
    )
