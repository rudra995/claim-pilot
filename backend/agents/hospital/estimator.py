"""Cost estimator: typical and high bill, and length of stay, from the trained cost model.

A case unlike the training data (an unknown procedure or room, or an age outside the range the
procedure was trained on) still gets an estimate, but a person checks it.
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.models.predict import estimate_cost
from backend.schemas import AgentOutput, Case, Finding
from data.generator.admissions import ROOMS
from data.generator.procedures import BY_SLUG

AGENT = "cost_estimator"
SOURCE = "Cost model (XGBoost quantile regression)"


class Estimate(BaseModel):
    bill_typical: int  # rupees, 50th percentile
    bill_high: int  # rupees, 90th percentile
    stay_typical: int  # days
    stay_high: int
    unusual: list[str]  # why this case is unlike the training data, if it is


def run(claim_id: str, case: Case) -> AgentResult:
    cost = estimate_cost(case)
    unusual = _unusual(case)
    findings = [
        Finding(issue="Typical bill", evidence=f"₹{cost.bill_typical:,} (50th percentile)", source=SOURCE),
        Finding(issue="High bill", evidence=f"₹{cost.bill_high:,} (90th percentile)", source=SOURCE),
        Finding(
            issue="Expected length of stay",
            evidence=f"{cost.stay_typical} days typical, {cost.stay_high} days high",
            source=SOURCE,
        ),
    ]
    findings += [Finding(issue="Unlike the training data", evidence=reason, source="Cost model training data") for reason in unusual]
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="unusual_case" if unusual else "estimated",
            confidence=0.4 if unusual else 0.9,
            findings=findings,
            next=HUMAN if unusual else "preauth",
        ),
        data=Estimate(
            bill_typical=cost.bill_typical,
            bill_high=cost.bill_high,
            stay_typical=cost.stay_typical,
            stay_high=cost.stay_high,
            unusual=unusual,
        ),
    )


def _unusual(case: Case) -> list[str]:
    admission = case.admission
    procedure = BY_SLUG.get(admission.procedure)
    reasons = []
    if procedure is None:
        reasons.append(f"Procedure '{admission.procedure}' was not in the training data")
    elif not procedure.age[0] <= case.patient.age <= procedure.age[1]:
        reasons.append(f"Age {case.patient.age} is outside the trained range {procedure.age[0]}-{procedure.age[1]}")
    if admission.room_category not in {slug for slug, _, _ in ROOMS}:
        reasons.append(f"Room category '{admission.room_category}' was not in the training data")
    return reasons
