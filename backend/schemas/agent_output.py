from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Finding(BaseModel):
    """One thing an agent noticed. A finding without a source is invalid."""

    model_config = ConfigDict(extra="forbid")

    issue: NonEmptyStr
    evidence: NonEmptyStr
    source: NonEmptyStr  # e.g. "Policy clause 4.2", "Bill line 3", "Discharge summary p.2"


class AgentOutput(BaseModel):
    """The shape every agent returns (design section 4)."""

    model_config = ConfigDict(extra="forbid")

    agent: NonEmptyStr
    claim_id: NonEmptyStr
    decision: NonEmptyStr
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = Field(default_factory=list)
    next: NonEmptyStr
