"""TPA query writer: turns the adjudicator's gaps into one specific request ("send the pre-op X-ray report")."""

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.agents.insurer.medical_review import Gap
from backend.schemas import AgentOutput, Finding

AGENT = "tpa_query_writer"


class QueryText(BaseModel):
    missing: str
    reason: str


def run(claim_id: str, gaps: list[Gap]) -> AgentResult:
    text = QueryText(
        missing="Please send: " + "; ".join(gap.missing for gap in gaps),
        reason=" ".join(gap.reason.rstrip(".") + "." for gap in gaps),
    )
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="query",
            confidence=1.0,
            findings=[Finding(issue=f"Requested: {gap.missing}", evidence=gap.reason, source="TPA adjudicator") for gap in gaps],
            next="reply",
        ),
        data=text,
    )
