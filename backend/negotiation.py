"""Plays one round of a claim between the hospital and the simulated TPA, for the CLI, tests and later the API.

The hospital sends the message a person approved, the insurer's graph answers on the exchange, and
the hospital acts on the answer. The two sides meet only here and on the exchange; neither imports
the other. Between rounds the claim usually waits for a person (backend/agents/approval.py).
"""

from pathlib import Path

from backend.agents import orchestrator
from backend.agents.insurer import graph as insurer
from backend.agents.orchestrator import RunResult
from backend.schemas import Case


def play_round(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """Take an approved (SUBMITTED) claim through one exchange with the insurer."""
    orchestrator.send(db_path, claim_id, case)
    insurer.review(db_path, claim_id)
    return orchestrator.receive(db_path, claim_id, case)
