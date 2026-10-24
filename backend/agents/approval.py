"""The human approval gate: a billing officer approves, edits, rejects or accepts a claim waiting for them.

Approve or edit lets the drafted message go to the insurer (SUBMITTED). Reject sends the draft back
to where it was made for rework: a pre-auth to PREAUTH_DRAFT, a response to QUERY_RAISED, an appeal
to the insurer's decision. Accept takes the insurer's latest decision as it stands, which is how a
person closes a negotiation that used all three rounds.
Each decision is written to the append-only audit log first, and the orchestrator will not move a
claim to SUBMITTED without one, so there is no path to the insurer that skips a person.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.agents.orchestrator import DECISION_STATE, claim_state, pending_draft, transition
from backend.db import audit
from backend.db.db import connect
from backend.exchange import messages as exchange
from backend.schemas import ClaimState, Final, Query, Sender
from backend.schemas.agent_output import NonEmptyStr


class Edits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_amount: int | None = Field(default=None, ge=0)
    justification: NonEmptyStr | None = None  # replaces the pre-auth justification, response or appeal text


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "edit", "reject", "accept"]
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
    pending = pending_draft(db_path, claim_id)
    if decision.decision in ("approve", "edit") and pending is None:
        raise ValueError(f"{claim_id}: nothing drafted to send; reject it or accept the insurer's decision")
    accepted = _insurer_decision(db_path, claim_id) if decision.decision == "accept" else None
    if decision.decision == "accept" and accepted is None:
        raise ValueError(f"{claim_id}: the insurer hasn't decided anything to accept")

    audit.log(db_path, claim_id, f"human:{decision.officer}", "human_decision", decision.model_dump(mode="json"))
    if decision.edits and decision.edits.requested_amount is not None:
        _save_requested(db_path, claim_id, decision.edits.requested_amount)

    if decision.decision == "accept":
        transition(db_path, claim_id, accepted, f"Insurer's decision accepted by {decision.officer}")
        return accepted
    if decision.decision == "reject":
        back = ClaimState(pending["from_state"]) if pending else ClaimState.PREAUTH_DRAFT
        transition(db_path, claim_id, back, f"Rejected by {decision.officer}: {decision.reason}")
        return back
    transition(db_path, claim_id, ClaimState.SUBMITTED, f"Approved by {decision.officer}")
    return ClaimState.SUBMITTED


def _insurer_decision(db_path: Path | str, claim_id: str) -> ClaimState | None:
    """The state matching the insurer's latest answer, if it was a decision rather than a query."""
    last = exchange.latest(db_path, claim_id)
    if last is None or last.sender != Sender.INSURER or isinstance(last, Query):
        return None
    return DECISION_STATE[last.decision if isinstance(last, Final) else last.type]


def _save_requested(db_path: Path | str, claim_id: str, amount: int) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE claims SET requested = ? WHERE claim_id = ?", (amount, claim_id))
    finally:
        conn.close()
