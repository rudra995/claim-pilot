"""Plays one round of a claim between the hospital and the simulated TPA, for the CLI, tests and later the API.

The hospital sends the message a person approved, the insurer's graph answers on the exchange, and
the hospital acts on the answer. The two sides meet only here and on the exchange; neither imports
the other. Between rounds the claim usually waits for a person (backend/agents/approval.py).
"""

from pathlib import Path

from backend.agents import orchestrator
from backend.agents.insurer import graph as insurer
from backend.agents.orchestrator import RunResult
from backend.exchange import messages as exchange
from backend.schemas import Case, ClaimState, Sender


def play_round(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """Take an approved (SUBMITTED) claim through one exchange with the insurer."""
    orchestrator.send(db_path, claim_id, case)
    insurer.review(db_path, claim_id)
    return orchestrator.receive(db_path, claim_id, case)


INTERRUPTIBLE = {ClaimState.ADMITTED, ClaimState.RISK_CHECK, ClaimState.QUERY_RAISED, ClaimState.DISCHARGE_DRAFT}


def resume(db_path: Path | str, claim_id: str, case: Case) -> RunResult | None:
    """Pick up a claim whose run stopped part-way (a crash, a restart), without repeating what was done.

    A SUBMITTED claim carries on from the last step recorded on the exchange: send the approved draft
    if it wasn't sent, get the insurer's answer if it hasn't come, act on it if it has. A claim
    stopped mid-way through the agents goes to a person, who can recheck it to rebuild the draft.
    """
    state = orchestrator.claim_state(db_path, claim_id)
    if state == ClaimState.SUBMITTED:
        last = exchange.latest(db_path, claim_id)
        if orchestrator.pending_draft(db_path, claim_id) is not None:
            return play_round(db_path, claim_id, case)
        if last is not None and last.sender == Sender.HOSPITAL:
            insurer.review(db_path, claim_id)
        return orchestrator.receive(db_path, claim_id, case)
    if state in INTERRUPTIBLE:
        orchestrator.transition(db_path, claim_id, ClaimState.AWAITING_HUMAN, f"Run interrupted at {state}: a person decides")
        return None
    raise orchestrator.InvalidTransition(f"{claim_id} is {state}; nothing to resume")
