"""Orchestrator: moves a claim through its states and calls the agents each state needs (design section 3).

Code decides the next step, never the LLM, and this is the only place a claim's state changes.
Each agent's output is checked against AgentOutput; a failed or invalid output is retried once,
then the claim goes straight to a person. Every agent output, retry, escalation and state change
is written to the audit log.

Admission flow:

    ADMITTED       document reader -> clinical coder
    PREAUTH_DRAFT  policy agent + cost estimator (in parallel) -> pre-auth agent
    RISK_CHECK     (the denial-risk agent joins here in phase 7)
    AWAITING_HUMAN billing officer approves, edits or rejects
"""

import json
import operator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.base import AgentResult
from backend.agents.hospital import coder, estimator, intake, policy, preauth
from backend.db import audit
from backend.db.cases import save_case
from backend.db.db import connect, init_db
from backend.schemas import AgentOutput, Case, ClaimState

ACTOR = "orchestrator"
MAX_ATTEMPTS = 2  # first try plus one retry (design section 4)

# Allowed moves. Later phases add the rest of the stay; any stage can hand the claim to a person.
TRANSITIONS: dict[ClaimState, set[ClaimState]] = {
    ClaimState.ADMITTED: {ClaimState.PREAUTH_DRAFT, ClaimState.AWAITING_HUMAN},
    ClaimState.PREAUTH_DRAFT: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN},
    ClaimState.RISK_CHECK: {ClaimState.AWAITING_HUMAN},
}

# The order agents run in, for printing a run.
ADMISSION_AGENTS = [intake.AGENT, coder.AGENT, policy.AGENT, estimator.AGENT, preauth.AGENT]


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class RunResult:
    claim_id: str
    state: ClaimState
    results: dict[str, AgentResult]
    review_flags: list[str]  # agents that asked for a person
    failed: list[str]  # agents that failed twice


# ---- State machine -------------------------------------------------------------------------


def open_claim(db_path: Path | str, case: Case) -> str:
    """Store the case and open a new claim on it in ADMITTED."""
    admission_id = case.admission.admission_id
    conn = init_db(db_path)
    try:
        save_case(conn, case)
        count = conn.execute("SELECT COUNT(*) FROM claims WHERE admission_id = ?", (admission_id,)).fetchone()[0]
        claim_id = f"CLM-{admission_id.removeprefix('ADM-')}-{count + 1}"
        with conn:
            conn.execute(
                "INSERT INTO claims (claim_id, admission_id, state) VALUES (?, ?, ?)",
                (claim_id, admission_id, ClaimState.ADMITTED.value),
            )
    finally:
        conn.close()
    audit.log(db_path, claim_id, ACTOR, "open_claim", {"case_id": case.case_id, "state": ClaimState.ADMITTED})
    return claim_id


def claim_state(db_path: Path | str, claim_id: str) -> ClaimState:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT state FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"No claim {claim_id}")
    return ClaimState(row["state"])


def transition(db_path: Path | str, claim_id: str, to: ClaimState, reason: str) -> None:
    """Move a claim to its next state, if the state machine allows it."""
    conn = connect(db_path)
    try:
        with conn:
            current = ClaimState(conn.execute("SELECT state FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()["state"])
            if to not in TRANSITIONS.get(current, set()):
                raise InvalidTransition(f"{claim_id}: {current} -> {to} is not allowed")
            conn.execute("UPDATE claims SET state = ? WHERE claim_id = ?", (to.value, claim_id))
    finally:
        conn.close()
    audit.log(db_path, claim_id, ACTOR, "transition", {"from": current, "to": to, "reason": reason})


# ---- Calling agents ------------------------------------------------------------------------


class _Run(TypedDict):
    db_path: str
    claim_id: str
    case: Case
    results: Annotated[dict[str, AgentResult], lambda a, b: {**a, **b}]
    review_flags: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]


def call_agent(db_path: Path | str, claim_id: str, name: str, run: Callable[..., AgentResult], *args: Any) -> AgentResult | None:
    """Run an agent, check its output, retry once; None means it failed twice and was escalated."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = run(claim_id, *args)
            _check(result, name, claim_id)
        except Exception as error:  # any failure, from a bad file to invalid output, is retried then escalated
            audit.log(db_path, claim_id, name, "agent_error", {"attempt": attempt, "error": repr(error)}, inputs=args)
            continue
        audit.log(
            db_path,
            claim_id,
            name,
            "agent_output",
            {"output": result.output.model_dump(mode="json"), "data": result.data.model_dump(mode="json")},
            inputs=args,
        )
        return result
    audit.log(db_path, claim_id, ACTOR, "escalate", {"agent": name, "reason": f"{name} failed {MAX_ATTEMPTS} times"})
    return None


def _check(result: AgentResult, name: str, claim_id: str) -> None:
    if not isinstance(result, AgentResult):
        raise TypeError(f"{name} returned {type(result).__name__}, not AgentResult")
    output = AgentOutput.model_validate(result.output.model_dump())  # re-check: catches outputs built without validation
    if output.agent != name or output.claim_id != claim_id:
        raise ValueError(f"output is labelled {output.agent}/{output.claim_id}, expected {name}/{claim_id}")


def _step(state: _Run, name: str, run: Callable[..., AgentResult], *args: Any) -> dict[str, Any]:
    result = call_agent(state["db_path"], state["claim_id"], name, run, *args)
    if result is None:
        return {"failed": [name]}
    update: dict[str, Any] = {"results": {name: result}}
    if result.wants_human:
        update["review_flags"] = [f"{name}: {result.output.decision}"]
    return update


# ---- Admission graph -----------------------------------------------------------------------


def _read_documents(state: _Run) -> dict[str, Any]:
    update = _step(state, intake.AGENT, intake.run, state["case"].documents)
    if intake.AGENT in update.get("results", {}):
        _save_extractions(state["db_path"], update["results"][intake.AGENT].data)
    return update


def _code_diagnosis(state: _Run) -> dict[str, Any]:
    reading = state["results"][intake.AGENT].data
    return _step(state, coder.AGENT, coder.run, reading, state["case"].admission)


def _start_draft(state: _Run) -> dict[str, Any]:
    transition(state["db_path"], state["claim_id"], ClaimState.PREAUTH_DRAFT, "Documents read and diagnosis coded")
    return {}


def _check_policy(state: _Run) -> dict[str, Any]:
    return _step(state, policy.AGENT, policy.run, state["case"])


def _estimate_cost(state: _Run) -> dict[str, Any]:
    return _step(state, estimator.AGENT, estimator.run, state["case"])


def _draft_preauth(state: _Run) -> dict[str, Any]:
    if state["failed"]:
        return {}
    results = state["results"]
    update = _step(
        state,
        preauth.AGENT,
        preauth.run,
        state["case"].admission,
        results[intake.AGENT].data,
        results[coder.AGENT].data,
        results[policy.AGENT].data,
        results[estimator.AGENT].data,
        state["review_flags"],
    )
    if preauth.AGENT in update.get("results", {}):
        _save_requested(state["db_path"], state["claim_id"], update["results"][preauth.AGENT].data.requested_amount)
    return update


def _risk_check(state: _Run) -> dict[str, Any]:
    transition(state["db_path"], state["claim_id"], ClaimState.RISK_CHECK, "Pre-auth package drafted")
    return {}


def _await_human(state: _Run) -> dict[str, Any]:
    transition(state["db_path"], state["claim_id"], ClaimState.AWAITING_HUMAN, "Pre-auth package needs approval")
    return {}


def _escalate(state: _Run) -> dict[str, Any]:
    reason = "Failed twice: " + ", ".join(state["failed"])
    transition(state["db_path"], state["claim_id"], ClaimState.AWAITING_HUMAN, reason)
    return {}


def _unless_failed(next_node: str) -> Callable[[_Run], str]:
    return lambda state: "escalate" if state["failed"] else next_node


@cache
def _admission_graph():
    graph = StateGraph(_Run)
    graph.add_node("document_reader", _read_documents)
    graph.add_node("clinical_coder", _code_diagnosis)
    graph.add_node("start_draft", _start_draft)
    graph.add_node("policy", _check_policy)
    graph.add_node("cost_estimator", _estimate_cost)
    graph.add_node("preauth", _draft_preauth)
    graph.add_node("risk_check", _risk_check)
    graph.add_node("await_human", _await_human)
    graph.add_node("escalate", _escalate)

    graph.add_edge(START, "document_reader")
    graph.add_conditional_edges("document_reader", _unless_failed("clinical_coder"), ["clinical_coder", "escalate"])
    graph.add_conditional_edges("clinical_coder", _unless_failed("start_draft"), ["start_draft", "escalate"])
    graph.add_edge("start_draft", "policy")  # policy and cost run in parallel; neither needs the other
    graph.add_edge("start_draft", "cost_estimator")
    graph.add_edge(["policy", "cost_estimator"], "preauth")
    graph.add_conditional_edges("preauth", _unless_failed("risk_check"), ["risk_check", "escalate"])
    graph.add_edge("risk_check", "await_human")
    graph.add_edge("await_human", END)
    graph.add_edge("escalate", END)
    return graph.compile()


def run_admission(case: Case, db_path: Path | str) -> RunResult:
    """Open a claim for the case and take it from ADMITTED to AWAITING_HUMAN."""
    claim_id = open_claim(db_path, case)
    final = _admission_graph().invoke(
        {"db_path": str(db_path), "claim_id": claim_id, "case": case, "results": {}, "review_flags": [], "failed": []}
    )
    return RunResult(
        claim_id=claim_id,
        state=claim_state(db_path, claim_id),
        results=final["results"],
        review_flags=final["review_flags"],
        failed=final["failed"],
    )


def _save_extractions(db_path: str, reading: intake.Reading) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.executemany(
                "UPDATE documents SET extracted_json = ? WHERE doc_id = ?",
                [(json.dumps(doc.model_dump(mode="json")), doc.doc_id) for doc in reading.documents],
            )
    finally:
        conn.close()


def _save_requested(db_path: str, claim_id: str, amount: int) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE claims SET requested = ? WHERE claim_id = ?", (amount, claim_id))
    finally:
        conn.close()
