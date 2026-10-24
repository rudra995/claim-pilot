"""The human approval gate: a billing officer approves, edits or rejects a claim waiting for them.

Approve or edit sends the claim on to SUBMITTED; reject sends it back to PREAUTH_DRAFT for rework.
Each decision is written to the append-only audit log first, and the orchestrator will not move a
claim to SUBMITTED without one, so there is no path to the insurer that skips a person.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.agents.orchestrator import claim_state, transition
from backend.db import audit
from backend.db.db import connect
from backend.schemas import ClaimState
from backend.schemas.agent_output import NonEmptyStr


class Edits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_amount: int | None = Field(default=None, ge=0)
    justification: NonEmptyStr | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "edit", "reject"]
    officer: NonEmptyStr  # who decided
    reason: NonEmptyStr | None = None  # required to reject
    edits: Edits | None = None  # required to edit

    @model_validator(mode="after")
    def _complete(self) -> "Decision":
        if self.decision == "reject" and not self.reason:
            raise ValueError("a rejection needs a reason")
        if self.decision == "edit" and (self.edits is None or not self.edits.model_dump(exclude_none=True)):
            raise ValueError("an edit needs at least one changed field")
        return self


def decide(db_path: Path | str, claim_id: str, decision: Decision) -> ClaimState:
    """Record the officer's decision and move the claim on. Returns the claim's new state."""
    state = claim_state(db_path, claim_id)
    if state != ClaimState.AWAITING_HUMAN:
        raise ValueError(f"{claim_id} is {state}, not waiting for a decision")
    audit.log(db_path, claim_id, f"human:{decision.officer}", "human_decision", decision.model_dump(mode="json"))
    if decision.edits and decision.edits.requested_amount is not None:
        _save_requested(db_path, claim_id, decision.edits.requested_amount)

    if decision.decision == "reject":
        transition(db_path, claim_id, ClaimState.PREAUTH_DRAFT, f"Rejected by {decision.officer}: {decision.reason}")
        return ClaimState.PREAUTH_DRAFT
    transition(db_path, claim_id, ClaimState.SUBMITTED, f"Approved by {decision.officer}")
    return ClaimState.SUBMITTED


def _save_requested(db_path: Path | str, claim_id: str, amount: int) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE claims SET requested = ? WHERE claim_id = ?", (amount, claim_id))
    finally:
        conn.close()
