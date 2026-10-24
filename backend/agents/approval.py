"""The human approval gate: a billing officer approves, edits, rejects or accepts a claim waiting for them.

Approve or edit lets the drafted message go to the insurer (SUBMITTED). Reject sends the draft back
to where it was made for rework: a pre-auth to PREAUTH_DRAFT, a response to QUERY_RAISED, an appeal
to the insurer's decision. Accept takes the insurer's latest decision as it stands, which is how a
person closes a negotiation that used all three rounds.
Each decision is written to the append-only audit log first, and the orchestrator will not move a
claim to SUBMITTED without one, so there is no path to the insurer that skips a person.

An approval is bound to the exact draft the person read: it records the draft's content hash, and
a caller that names the hash it reviewed (`draft_hash`) is refused if the draft has changed since.
A draft rebuilt after new evidence is a new version and needs its own approval.

`identity` says how the officer's name was established: "self-declared" in the demo (no login), or
"token" when the API maps a configured access token to a name. The demo has no real authentication.
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
    draft_hash: str | None = None  # the draft version the officer reviewed; refused if it has changed
    identity: Literal["self-declared", "token"] = "self-declared"

    @model_validator(mode="after")
    def _complete(self) -> "Decision":
        if self.decision == "reject" and not self.reason:
            raise ValueError("a rejection needs a reason")
        if self.decision == "edit" and (self.edits is None or not self.edits.model_dump(exclude_none=True)):
            raise ValueError("an edit needs at least one changed field")
        return self


class DraftChanged(ValueError):
    """The officer reviewed a different version of the draft than the one now pending."""


def decide(db_path: Path | str, claim_id: str, decision: Decision) -> ClaimState:
    """Record the officer's decision and move the claim on. Returns the claim's new state."""
    state = claim_state(db_path, claim_id)
    if state != ClaimState.AWAITING_HUMAN:
        raise ValueError(f"{claim_id} is {state}, not waiting for a decision")
    pending = pending_draft(db_path, claim_id)
    if decision.decision in ("approve", "edit") and pending is None:
        raise ValueError(f"{claim_id}: nothing drafted to send; reject it or accept the insurer's decision")
    if decision.draft_hash and pending is not None and decision.draft_hash != pending["hash"]:
        raise DraftChanged(f"{claim_id}: the draft changed to v{pending['version']} since it was reviewed; review it again")
    accepted = _insurer_decision(db_path, claim_id) if decision.decision == "accept" else None
    if decision.decision == "accept" and accepted is None:
        raise ValueError(f"{claim_id}: the insurer hasn't decided anything to accept")

    record = decision.model_dump(mode="json")
    if pending is not None:  # bind the decision to the exact draft it was made on
        record |= {"draft_hash": pending["hash"], "draft_version": pending["version"]}
    audit.log(db_path, claim_id, f"human:{decision.officer}", "human_decision", record)
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
