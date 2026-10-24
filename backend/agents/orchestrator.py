"""Orchestrator: moves a claim through its states and calls the agents each state needs (design section 3).

Code decides the next step, never the LLM, and this is the only place a claim's state changes.
Each agent's output is checked against AgentOutput; a failed or invalid output is retried once,
then the claim goes straight to a person. Every agent output, retry, escalation and state change
is written to the audit log.

Admission flow:

    ADMITTED       document reader -> clinical coder
    PREAUTH_DRAFT  policy agent + cost estimator (in parallel) -> pre-auth agent
    RISK_CHECK     denial-risk agent
    AWAITING_HUMAN billing officer approves, edits or rejects (backend/agents/approval.py)
    SUBMITTED      only with an approval recorded since the claim last reached AWAITING_HUMAN

Negotiation (design section 5). Every message to the insurer is drafted first and goes out only
after a person approves it (`send`). The insurer's answer comes back through the exchange
(`receive`), and the hospital never talks to the insurer's agents directly:

    QUERY_RAISED       query responder drafts a RESPONSE -> RISK_CHECK -> AWAITING_HUMAN
    DENIED / PARTIALLY_APPROVED
                       appeal agent drafts an APPEAL -> RISK_CHECK -> AWAITING_HUMAN,
                       accepts the decision if the policy supports every deduction,
                       or, for a whole-claim denial, hands it to a person (AWAITING_HUMAN)
    APPROVED           done for this stage
    after round 3      no round is left: AWAITING_HUMAN, and a person decides

Rest of the stay (design section 4). Each stage's decision moves the claim on (`next_stage`):

    IN_TREATMENT       enhancement agent watches the bill (`watch_bill`); past the approval it drafts
                       an enhancement -> RISK_CHECK -> AWAITING_HUMAN -> SUBMITTED -> insurer
    DISCHARGE_DRAFT    discharge agent drafts the final package (`discharge`) -> RISK_CHECK -> AWAITING_HUMAN
    FINAL_APPROVED     only on the insurer's discharge decision
    SETTLED            only with a payment advice recorded (`settle`)
    CLOSED             reconciliation agent finds nothing to chase (`reconcile`)

Agents call the LLM only through backend.llm, which masks the patient's name and ids for the
claim being worked on and logs the masked prompt (design section 9).
"""

import hashlib
import json
import operator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.base import AgentResult
from backend import llm
from backend.agents.hospital import (
    appeal,
    coder,
    denial_risk,
    discharge as discharge_agent,
    enhancement,
    estimator,
    intake,
    patient,
    policy,
    preauth,
    query_responder,
    reconcile as reconcile_agent,
)
from backend.agents.hospital.intake import Reading, ReadDocument
from backend.db import audit
from backend.db.cases import save_case
from backend.db.db import connect, init_db
from backend.exchange import messages as exchange
from backend.privacy import Masker
from backend.remediation import corrections
from backend.stages import available
from backend.schemas import (
    MAX_ROUNDS,
    AgentOutput,
    Appeal,
    Approve,
    Case,
    ClaimMessage,
    ClaimState,
    Deny,
    Final,
    MessageType,
    PartialApprove,
    Query,
    Response,
    Sender,
    Stage,
    Submit,
    claim_message_adapter,
)

ACTOR = "orchestrator"
MAX_ATTEMPTS = 2  # first try plus one retry (design section 4)

_DECIDED = {ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED, ClaimState.DENIED}

# Allowed moves. Later phases add the rest of the stay; any stage can hand the claim to a person.
TRANSITIONS: dict[ClaimState, set[ClaimState]] = {
    ClaimState.ADMITTED: {ClaimState.PREAUTH_DRAFT, ClaimState.AWAITING_HUMAN},
    ClaimState.PREAUTH_DRAFT: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN},
    ClaimState.RISK_CHECK: {ClaimState.AWAITING_HUMAN},
    # approved; rejected back to where the draft was made; or the insurer's decision accepted
    ClaimState.AWAITING_HUMAN: {
        ClaimState.SUBMITTED, ClaimState.PREAUTH_DRAFT, ClaimState.QUERY_RAISED, ClaimState.IN_TREATMENT,
        ClaimState.DISCHARGE_DRAFT, *_DECIDED,
    },
    ClaimState.SUBMITTED: {ClaimState.QUERY_RAISED, *_DECIDED},
    ClaimState.QUERY_RAISED: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN},
    # a decided stage moves on: back to treatment, or to final approval after the discharge stage
    ClaimState.APPROVED: {ClaimState.IN_TREATMENT, ClaimState.FINAL_APPROVED},
    ClaimState.PARTIALLY_APPROVED: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN, ClaimState.IN_TREATMENT, ClaimState.FINAL_APPROVED},
    ClaimState.DENIED: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN},
    ClaimState.IN_TREATMENT: {ClaimState.RISK_CHECK, ClaimState.DISCHARGE_DRAFT, ClaimState.AWAITING_HUMAN},
    ClaimState.DISCHARGE_DRAFT: {ClaimState.RISK_CHECK, ClaimState.AWAITING_HUMAN},
    ClaimState.FINAL_APPROVED: {ClaimState.SETTLED},
    ClaimState.SETTLED: {ClaimState.CLOSED},
}
APPROVING = {"approve", "edit"}  # decisions that let a claim be submitted
DECISION_STATE = {
    MessageType.APPROVE: ClaimState.APPROVED,
    MessageType.PARTIAL_APPROVE: ClaimState.PARTIALLY_APPROVED,
    MessageType.DENY: ClaimState.DENIED,
}

REFUSED = "appeal: the insurer already refused this appeal"

# The order agents run in, for printing a run.
ADMISSION_AGENTS = [intake.AGENT, coder.AGENT, policy.AGENT, estimator.AGENT, preauth.AGENT, denial_risk.AGENT]


class InvalidTransition(ValueError):
    pass


class ApprovalRequired(InvalidTransition):
    """Nothing goes to the insurer without a person's approval (design section 9)."""


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
            if to == ClaimState.SUBMITTED and approval(db_path, claim_id) is None:
                raise ApprovalRequired(f"{claim_id}: no approval recorded since it reached {ClaimState.AWAITING_HUMAN}")
            if to == ClaimState.FINAL_APPROVED:
                last = exchange.latest(db_path, claim_id)
                if last is None or last.stage != Stage.DISCHARGE or last.sender != Sender.INSURER:
                    raise InvalidTransition(f"{claim_id}: final approval needs the insurer's discharge decision")
            if to == ClaimState.SETTLED and payment(db_path, claim_id) is None:
                raise InvalidTransition(f"{claim_id}: no payment advice recorded")
            conn.execute("UPDATE claims SET state = ? WHERE claim_id = ?", (to.value, claim_id))
    finally:
        conn.close()
    audit.log(db_path, claim_id, ACTOR, "transition", {"from": current, "to": to, "reason": reason})


def approval(db_path: Path | str, claim_id: str) -> dict[str, Any] | None:
    """The approving decision recorded since the claim last reached AWAITING_HUMAN, if any.

    An approval counts only for the exact draft it was given on: it carries the draft's content hash,
    and if the pending draft has changed since, the approval no longer applies.
    """
    decision = None
    for event in audit.events(db_path, claim_id):
        if event["action"] == "transition" and event["output"]["to"] == ClaimState.AWAITING_HUMAN:
            decision = None  # a claim back in the queue needs a fresh decision
        elif event["action"] == "human_decision":
            decision = event["output"] if event["output"]["decision"] in APPROVING else None
    if decision is not None and decision.get("draft_hash"):
        pending = pending_draft(db_path, claim_id)
        if pending is None or pending["hash"] != decision["draft_hash"]:
            return None
    return decision


def draft_hash(message: dict[str, Any]) -> str:
    """A short fingerprint of a drafted message's exact content."""
    return hashlib.sha256(json.dumps(message, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def draft(db_path: Path | str, claim_id: str, message: ClaimMessage, from_state: ClaimState) -> None:
    """Hold a message for the insurer until a person approves it; `from_state` is where a rejection returns it.

    Each draft gets the next version number and a hash of its content, so an approval can name the
    exact version a person read. Earlier drafts stay in the audit log; a new one supersedes them.
    """
    body = message.model_dump(mode="json")
    version = 1 + sum(e["action"] == "draft_message" for e in audit.events(db_path, claim_id))
    audit.log(db_path, claim_id, ACTOR, "draft_message", {"from_state": from_state, "message": body, "version": version, "hash": draft_hash(body)})


def pending_draft(db_path: Path | str, claim_id: str) -> dict[str, Any] | None:
    """The drafted message waiting to be sent, if any: {"from_state", "message", "version", "hash"}."""
    pending = None
    version = 0
    for event in audit.events(db_path, claim_id):
        if event["action"] == "draft_message":
            version += 1
            pending = {"version": version, "hash": draft_hash(event["output"]["message"]), **event["output"]}
        elif event["action"] == "message_sent" and event["actor"] == Sender.HOSPITAL:
            pending = None
        elif event["action"] == "human_decision" and event["output"]["decision"] not in APPROVING:
            pending = None  # rejected, or the insurer's decision accepted: the draft is dropped
    return pending


def drafts(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """Every draft on the claim, oldest first, with what became of it: pending, superseded, rejected or sent."""
    out: list[dict[str, Any]] = []
    for event in audit.events(db_path, claim_id):
        if event["action"] == "draft_message":
            if out and out[-1]["status"] == "pending":
                out[-1]["status"] = "superseded"
            body = event["output"]["message"]
            out.append({
                "version": len(out) + 1, "hash": draft_hash(body), "type": body["type"], "stage": body["stage"],
                "round": body["round"], "created_at": event["created_at"], "status": "pending", "approved_by": None,
            })
        elif out and out[-1]["status"] == "pending":
            if event["action"] == "human_decision":
                decision = event["output"]
                if decision["decision"] in APPROVING:
                    out[-1]["approved_by"] = decision["officer"]
                else:
                    out[-1]["status"] = "rejected" if decision["decision"] == "reject" else "dropped"
            elif event["action"] == "message_sent" and event["actor"] == Sender.HOSPITAL:
                out[-1]["status"] = "sent"
    return out


# ---- Calling agents ------------------------------------------------------------------------


class _Run(TypedDict):
    db_path: str
    claim_id: str
    case: Case
    results: Annotated[dict[str, AgentResult], lambda a, b: {**a, **b}]
    review_flags: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]


def call_agent(
    db_path: Path | str, claim_id: str, name: str, run: Callable[..., AgentResult], *args: Any, masker: Masker | None = None
) -> AgentResult | None:
    """Run an agent, check its output, retry once; None means it failed twice and was escalated.

    Any LLM call the agent makes is masked with `masker` and logged against the claim.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with llm.for_claim(db_path, claim_id, name, masker or Masker()):
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
    masker = Masker.for_case(state["case"])
    result = call_agent(state["db_path"], state["claim_id"], name, run, *args, masker=masker)
    if result is None:
        return {"failed": [name]}
    update: dict[str, Any] = {"results": {name: result}}
    if result.wants_human:
        update["review_flags"] = [f"{name}: {result.output.decision}"]
    return update


# ---- Admission graph -----------------------------------------------------------------------


def _read_documents(state: _Run) -> dict[str, Any]:
    update = _step(state, intake.AGENT, intake.run, available(state["case"].documents, Stage.PREAUTH))
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
        _draft_submit(state["db_path"], state["claim_id"], update["results"][preauth.AGENT].data)
    return update


def _draft_submit(db_path: Path | str, claim_id: str, package: preauth.PreAuthPackage) -> None:
    _save_requested(str(db_path), claim_id, package.requested_amount)
    submit = Submit(
        claim_id=claim_id,
        stage=Stage.PREAUTH,
        round=1,
        package=package.model_dump(mode="json"),
        documents=package.documents,
        requested_amount=package.requested_amount,
    )
    draft(db_path, claim_id, submit, ClaimState.PREAUTH_DRAFT)


def _risk_check(state: _Run) -> dict[str, Any]:
    transition(state["db_path"], state["claim_id"], ClaimState.RISK_CHECK, "Pre-auth package drafted")
    reading = state["results"][intake.AGENT].data
    return _step(state, denial_risk.AGENT, denial_risk.run, state["case"], reading, Stage.PREAUTH)


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
    graph.add_conditional_edges("risk_check", _unless_failed("await_human"), ["await_human", "escalate"])
    graph.add_edge("await_human", END)
    graph.add_edge("escalate", END)
    return graph.compile()


def run_admission(case: Case, db_path: Path | str) -> RunResult:
    """Open a claim for the case and take it from ADMITTED to AWAITING_HUMAN."""
    return admit(db_path, open_claim(db_path, case), case)


def admit(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """Take a claim already opened with `open_claim` from ADMITTED to AWAITING_HUMAN."""
    _require(db_path, claim_id, ClaimState.ADMITTED)
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


# ---- Negotiation ---------------------------------------------------------------------------


def send(db_path: Path | str, claim_id: str, case: Case) -> ClaimMessage:
    """Post the approved draft to the exchange, with the officer's edits applied.

    A pre-auth SUBMIT carries the claim file (the case) for the insurer to review.
    """
    if (state := claim_state(db_path, claim_id)) != ClaimState.SUBMITTED:
        raise InvalidTransition(f"{claim_id} is {state}; only an approved (SUBMITTED) claim is sent")
    pending = pending_draft(db_path, claim_id)
    if pending is None:
        raise ValueError(f"{claim_id}: nothing drafted to send")
    approved = approval(db_path, claim_id)
    if approved is None:
        raise ApprovalRequired(f"{claim_id}: the approval doesn't match draft v{pending['version']}; it needs a fresh decision")
    edits = approved.get("edits") or {}
    message = claim_message_adapter.validate_python(pending["message"])
    if isinstance(message, Submit):
        package = {**message.package, **({"justification": edits["justification"]} if edits.get("justification") else {})}
        message = message.model_copy(update={
            "package": {message.stage.value: package, "case": case.model_dump(mode="json")},
            "requested_amount": edits.get("requested_amount") or message.requested_amount,
        })
    elif edits.get("justification"):
        message = message.model_copy(update={"answer" if isinstance(message, Response) else "argument": edits["justification"]})
    if isinstance(message, (Submit, Response, Appeal)):  # the files go with the message, including ones attached since
        sent = message.cited_documents if isinstance(message, Appeal) else message.documents
        exchange.attach(db_path, claim_id, [doc for doc in case.documents if doc.doc_id in sent])
    exchange.post(db_path, message)
    return message


class _Reply(TypedDict):
    db_path: str
    claim_id: str
    case: Case
    reply: ClaimMessage
    results: Annotated[dict[str, AgentResult], lambda a, b: {**a, **b}]
    review_flags: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]


def receive(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """Act on the insurer's answer: record its decision, then draft a response or appeal if one is needed."""
    reply = exchange.latest(db_path, claim_id)
    if reply is None or reply.sender != Sender.INSURER:
        raise exchange.ProtocolError(f"{claim_id}: no answer from the insurer yet")
    if (state := claim_state(db_path, claim_id)) != ClaimState.SUBMITTED:
        raise InvalidTransition(f"{claim_id} is {state}, not waiting for the insurer")
    final = _reply_graph().invoke(
        {"db_path": str(db_path), "claim_id": claim_id, "case": case, "reply": reply, "results": {}, "review_flags": [], "failed": []}
    )
    return RunResult(
        claim_id=claim_id,
        state=claim_state(db_path, claim_id),
        results=final["results"],
        review_flags=final["review_flags"],
        failed=final["failed"],
    )


def _record_decision(state: _Reply) -> dict[str, Any]:
    reply, db_path, claim_id = state["reply"], state["db_path"], state["claim_id"]
    if isinstance(reply, Query):
        transition(db_path, claim_id, ClaimState.QUERY_RAISED, f"Insurer query, round {reply.round}: {reply.missing}")
        return {}
    decision = reply.decision if isinstance(reply, Final) else reply.type
    approved = 0 if isinstance(reply, Deny) else reply.approved_amount
    _save_approved(db_path, claim_id, approved)
    transition(db_path, claim_id, DECISION_STATE[decision], f"Insurer {reply.type}, round {reply.round}: ₹{approved:,} approved")
    return {}


def _after_decision(state: _Reply) -> str:
    reply = state["reply"]
    if isinstance(reply, Approve) or (isinstance(reply, Final) and reply.decision == MessageType.APPROVE):
        return END
    if isinstance(reply, Final) or reply.round >= MAX_ROUNDS:
        return "round_limit"
    return "query_responder" if isinstance(reply, Query) else "appeal"


def _answer_query(state: _Reply) -> dict[str, Any]:
    reply = state["reply"]
    update = _step(state, query_responder.AGENT, query_responder.run, reply, _reading(state["db_path"], state["case"]), reply.round + 1)
    if query_responder.AGENT in update.get("results", {}):
        draft(state["db_path"], state["claim_id"], update["results"][query_responder.AGENT].data, ClaimState.QUERY_RAISED)
    return update


def _appeal(state: _Reply) -> dict[str, Any]:
    reply, db_path, claim_id = state["reply"], state["db_path"], state["claim_id"]
    reading, requested = _reading(db_path, state["case"]), _requested(db_path, claim_id)
    update = _step(state, appeal.AGENT, appeal.run, reply, state["case"], reading, requested, reply.round + 1)
    if appeal.AGENT in update.get("results", {}) and (letter := update["results"][appeal.AGENT].data.appeal):
        if _already_refused(db_path, claim_id, reply, letter):
            audit.log(db_path, claim_id, ACTOR, "escalate", {"agent": appeal.AGENT, "reason": "The insurer already refused this appeal; a person decides instead of sending it again"})
            return {**update, "review_flags": [*update.get("review_flags", []), REFUSED]}
        draft(db_path, claim_id, letter, claim_state(db_path, claim_id))
    return update


def _already_refused(db_path: Path | str, claim_id: str, reply: ClaimMessage, letter: Appeal) -> bool:
    """An earlier appeal in this stage contested the same clauses, and the insurer paid no more after it."""
    stage = exchange.thread(db_path, claim_id, reply.stage)
    for earlier, answer in zip(stage, stage[1:]):
        if isinstance(earlier, Appeal) and set(earlier.cited_clauses) == set(letter.cited_clauses):
            before = next((m for m in reversed(stage[:stage.index(earlier)]) if m.sender == Sender.INSURER), None)
            if before is not None and getattr(answer, "approved_amount", 0) <= getattr(before, "approved_amount", 0):
                return True
    return False


def _after_appeal(state: _Reply) -> str:
    if state["failed"]:
        return "escalate"
    if REFUSED in state["review_flags"]:
        return "denied"
    result = state["results"][appeal.AGENT]
    if result.data.appeal:
        return "risk_check"
    return "denied" if result.wants_human else END  # END: nothing to appeal, decision accepted


def _denied(state: _Reply) -> dict[str, Any]:
    if isinstance(state["reply"], Deny):
        reason = "Claim denied: a person decides whether to appeal"
    elif REFUSED in state["review_flags"]:
        reason = "The insurer already refused this appeal: a person decides"
    else:
        reason = "Cut not appealed: the records don't support an appeal, so a person decides"
    transition(state["db_path"], state["claim_id"], ClaimState.AWAITING_HUMAN, reason)
    return {}


def _check_reply(state: _Reply) -> dict[str, Any]:
    kind = "Response" if isinstance(state["reply"], Query) else "Appeal"
    transition(state["db_path"], state["claim_id"], ClaimState.RISK_CHECK, f"{kind} to the insurer drafted")
    reading = _reading(state["db_path"], state["case"])
    return _step(state, denial_risk.AGENT, denial_risk.run, state["case"], reading, state["reply"].stage)


def _reply_needs_human(state: _Reply) -> dict[str, Any]:
    transition(state["db_path"], state["claim_id"], ClaimState.AWAITING_HUMAN, "Reply to the insurer needs approval")
    return {}


def _round_limit(state: _Reply) -> dict[str, Any]:
    reason = f"{MAX_ROUNDS} rounds used without full approval: a person decides"
    transition(state["db_path"], state["claim_id"], ClaimState.AWAITING_HUMAN, reason)
    return {"review_flags": [reason]}


@cache
def _reply_graph():
    graph = StateGraph(_Reply)
    graph.add_node("record_decision", _record_decision)
    graph.add_node("query_responder", _answer_query)
    graph.add_node("appeal", _appeal)
    graph.add_node("risk_check", _check_reply)
    graph.add_node("await_human", _reply_needs_human)
    graph.add_node("round_limit", _round_limit)
    graph.add_node("denied", _denied)
    graph.add_node("escalate", _escalate)

    graph.add_edge(START, "record_decision")
    graph.add_conditional_edges("record_decision", _after_decision, [END, "round_limit", "query_responder", "appeal"])
    graph.add_conditional_edges("query_responder", _unless_failed("risk_check"), ["risk_check", "escalate"])
    graph.add_conditional_edges("appeal", _after_appeal, ["risk_check", "denied", "escalate", END])
    graph.add_conditional_edges("risk_check", _unless_failed("await_human"), ["await_human", "escalate"])
    graph.add_edge("await_human", END)
    graph.add_edge("round_limit", END)
    graph.add_edge("denied", END)
    graph.add_edge("escalate", END)
    return graph.compile()


# ---- Rest of the stay ----------------------------------------------------------------------


def next_stage(db_path: Path | str, claim_id: str) -> ClaimState:
    """After a stage is decided: back to treatment, or, after the discharge stage, final approval."""
    state = claim_state(db_path, claim_id)
    if state not in (ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED):
        raise InvalidTransition(f"{claim_id} is {state}; only an approved stage moves on")
    last = exchange.latest(db_path, claim_id)
    if last is not None and last.stage == Stage.DISCHARGE:
        transition(db_path, claim_id, ClaimState.FINAL_APPROVED, f"Final approval: ₹{_approved(db_path, claim_id):,}")
        return ClaimState.FINAL_APPROVED
    stage = last.stage if last else Stage.PREAUTH
    transition(db_path, claim_id, ClaimState.IN_TREATMENT, f"{stage.value.capitalize()} decided: treatment goes on")
    return ClaimState.IN_TREATMENT


def watch_bill(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """The running bill has changed: store it, and ask for more if it has outgrown the approval."""
    _require(db_path, claim_id, ClaimState.IN_TREATMENT)
    conn = connect(db_path)
    try:
        save_case(conn, case)  # adds the new bill lines
    finally:
        conn.close()
    sent = [m for m in exchange.thread(db_path, claim_id) if isinstance(m, Submit)]
    last_sent = Case.model_validate(sent[-1].package["case"]) if sent and "case" in sent[-1].package else None
    used = any(m.stage == Stage.ENHANCEMENT for m in sent)
    result = call_agent(
        db_path, claim_id, enhancement.AGENT, enhancement.run, case, _approved(db_path, claim_id), last_sent, used,
        masker=Masker.for_case(case),
    )
    if result is None:
        return _queue_for_person(db_path, claim_id, {}, [enhancement.AGENT], "Enhancement agent failed twice")
    results = {enhancement.AGENT: result}
    if result.data.submit is not None:
        _save_requested(str(db_path), claim_id, result.data.submit.requested_amount)
        draft(db_path, claim_id, result.data.submit, ClaimState.IN_TREATMENT)
        return _risk_check_and_queue(db_path, claim_id, case, results, "Enhancement request drafted", Stage.ENHANCEMENT)
    if result.wants_human:
        return _queue_for_person(db_path, claim_id, results, [], "Bill above the approval: a person decides")
    return RunResult(claim_id, claim_state(db_path, claim_id), results, [], [])


def discharge(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """The patient is going home: draft the final package with its deduction preview."""
    _require(db_path, claim_id, ClaimState.IN_TREATMENT)
    transition(db_path, claim_id, ClaimState.DISCHARGE_DRAFT, "Patient ready for discharge")
    masker = Masker.for_case(case)
    read = call_agent(db_path, claim_id, intake.AGENT, intake.run, case.documents, masker=masker)  # now the later records exist
    if read is None:
        return _queue_for_person(db_path, claim_id, {}, [intake.AGENT], "Document reader failed twice")
    _save_extractions(str(db_path), read.data)
    result = call_agent(
        db_path, claim_id, discharge_agent.AGENT, discharge_agent.run, case, _reading(db_path, case), masker=Masker.for_case(case)
    )
    if result is None:
        return _queue_for_person(db_path, claim_id, {}, [discharge_agent.AGENT], "Discharge agent failed twice")
    _save_requested(str(db_path), claim_id, result.data.submit.requested_amount)
    draft(db_path, claim_id, result.data.submit, ClaimState.DISCHARGE_DRAFT)
    return _risk_check_and_queue(db_path, claim_id, case, {intake.AGENT: read, discharge_agent.AGENT: result}, "Final package drafted", Stage.DISCHARGE)


def redraft(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """A pre-auth a person sent back: code, price and draft it again from the case as it now stands."""
    _require(db_path, claim_id, ClaimState.PREAUTH_DRAFT)
    return _redraft(db_path, claim_id, case, _reading(db_path, case))


def _redraft(db_path: Path | str, claim_id: str, case: Case, reading: Reading, results: dict[str, AgentResult] | None = None) -> RunResult:
    masker = Masker.for_case(case)
    results = dict(results or {})
    for name, run, args in [
        (coder.AGENT, coder.run, (reading, case.admission)),
        (policy.AGENT, policy.run, (case,)),
        (estimator.AGENT, estimator.run, (case,)),
    ]:
        if (result := call_agent(db_path, claim_id, name, run, *args, masker=masker)) is None:
            return _queue_for_person(db_path, claim_id, results, [name], f"{name} failed twice")
        results[name] = result
    flags = [f"{name}: {r.output.decision}" for name, r in results.items() if r.wants_human]
    package = call_agent(
        db_path, claim_id, preauth.AGENT, preauth.run, case.admission, reading,
        results[coder.AGENT].data, results[policy.AGENT].data, results[estimator.AGENT].data, flags, masker=masker,
    )
    if package is None:
        return _queue_for_person(db_path, claim_id, results, [preauth.AGENT], "Pre-auth agent failed twice")
    _draft_submit(db_path, claim_id, package.data)
    return _risk_check_and_queue(db_path, claim_id, case, {**results, preauth.AGENT: package}, "Pre-auth redrafted")


# ---- Fix and recheck -----------------------------------------------------------------------

RECHECKABLE = {ClaimState.PREAUTH_DRAFT, ClaimState.QUERY_RAISED, ClaimState.DISCHARGE_DRAFT}


def open_issues(db_path: Path | str, claim_id: str) -> list[str]:
    """What the latest checks say still needs fixing: risk items, unread or conflicting fields, held-back appeals."""
    latest: dict[str, dict[str, Any]] = {}
    for event in audit.events(db_path, claim_id):
        if event["action"] == "agent_output":
            latest[event["actor"]] = event["output"]
    issues = []
    if risk := latest.get(denial_risk.AGENT):
        issues += [item["issue"] for item in risk["data"]["items"]]
    if reader := latest.get(intake.AGENT):
        problems = ("Not found", "Conflicting", "Scanned", "Instruction", "Check:", "Low confidence", "Blank field")
        issues += [f"{f['issue']} ({f['source']})" for f in reader["output"]["findings"] if f["issue"].startswith(problems)]
    return list(dict.fromkeys(issues))


def appeal_recheckable(db_path: Path | str, claim_id: str) -> bool:
    """The insurer cut the claim, a round is left, and the agents didn't appeal: new evidence could change that."""
    last = exchange.latest(db_path, claim_id)
    return isinstance(last, PartialApprove) and last.round < MAX_ROUNDS


def recheck(db_path: Path | str, claim_id: str, case: Case, reason: str) -> RunResult:
    """New evidence or a correction: re-read the documents, rerun what depends on them, and draft a new version.

    The old draft is superseded, not edited, and any approval given on it no longer counts: the new
    version goes back to a person. What changed is written to the audit log as `recheck`.
    """
    state = claim_state(db_path, claim_id)
    pending = pending_draft(db_path, claim_id)
    if state == ClaimState.AWAITING_HUMAN and pending and ClaimState(pending["from_state"]) in RECHECKABLE:
        back = ClaimState(pending["from_state"])
        transition(db_path, claim_id, back, f"Rechecking: {reason}")
    elif state == ClaimState.PREAUTH_DRAFT:
        back = state
    elif state == ClaimState.AWAITING_HUMAN and pending is None and appeal_recheckable(db_path, claim_id):
        back = ClaimState.PARTIALLY_APPROVED  # a cut the agents held back from appealing: look at the evidence again
        transition(db_path, claim_id, back, f"Rechecking the evidence for an appeal: {reason}")
    else:
        raise InvalidTransition(f"{claim_id} is {state}; only a draft waiting for a person, a sent-back pre-auth, or a cut not yet appealed can be rechecked")
    before = open_issues(db_path, claim_id)
    version = pending["version"] if pending else None

    last = exchange.latest(db_path, claim_id)
    stage = {ClaimState.PREAUTH_DRAFT: Stage.PREAUTH, ClaimState.DISCHARGE_DRAFT: Stage.DISCHARGE}.get(back, last.stage if last else Stage.PREAUTH)
    masker = Masker.for_case(case)
    read = call_agent(db_path, claim_id, intake.AGENT, intake.run, available(case.documents, stage), corrections(db_path, claim_id), masker=masker)
    if read is None:
        return _queue_for_person(db_path, claim_id, {}, [intake.AGENT], "Document reader failed twice")
    _save_extractions(str(db_path), read.data)
    results = {intake.AGENT: read}

    if back == ClaimState.PREAUTH_DRAFT:
        result = _redraft(db_path, claim_id, case, read.data, results)
    elif back == ClaimState.QUERY_RAISED:
        query = next(m for m in reversed(exchange.thread(db_path, claim_id)) if isinstance(m, Query))
        answer = call_agent(db_path, claim_id, query_responder.AGENT, query_responder.run, query, _reading(db_path, case), query.round + 1, masker=masker)
        if answer is None:
            return _queue_for_person(db_path, claim_id, results, [query_responder.AGENT], "Query responder failed twice")
        draft(db_path, claim_id, answer.data, ClaimState.QUERY_RAISED)
        result = _risk_check_and_queue(db_path, claim_id, case, {**results, query_responder.AGENT: answer}, "Response redrafted", query.stage)
    elif back == ClaimState.PARTIALLY_APPROVED:
        cut = exchange.latest(db_path, claim_id)
        argued = call_agent(db_path, claim_id, appeal.AGENT, appeal.run, cut, case, _reading(db_path, case), _requested(db_path, claim_id), cut.round + 1, masker=masker)
        if argued is not None and argued.data.appeal is not None:
            draft(db_path, claim_id, argued.data.appeal, ClaimState.PARTIALLY_APPROVED)
            result = _risk_check_and_queue(db_path, claim_id, case, {**results, appeal.AGENT: argued}, "Appeal drafted on the new evidence", cut.stage)
        else:
            result = _queue_for_person(db_path, claim_id, results, [] if argued else [appeal.AGENT], "Still no evidence that supports an appeal: a person decides")
    else:
        final = call_agent(db_path, claim_id, discharge_agent.AGENT, discharge_agent.run, case, _reading(db_path, case), masker=masker)
        if final is None:
            return _queue_for_person(db_path, claim_id, results, [discharge_agent.AGENT], "Discharge agent failed twice")
        _save_requested(str(db_path), claim_id, final.data.submit.requested_amount)
        draft(db_path, claim_id, final.data.submit, ClaimState.DISCHARGE_DRAFT)
        result = _risk_check_and_queue(db_path, claim_id, case, {**results, discharge_agent.AGENT: final}, "Final package redrafted", Stage.DISCHARGE)

    after = open_issues(db_path, claim_id)
    now = pending_draft(db_path, claim_id)
    audit.log(db_path, claim_id, ACTOR, "recheck", {
        "reason": reason,
        "resolved": [issue for issue in before if issue not in after],
        "remaining": [issue for issue in after if issue in before],
        "new": [issue for issue in after if issue not in before],
        "draft_before": version,
        "draft_after": now["version"] if now else None,
    })
    return result


def settle(db_path: Path | str, claim_id: str, paid: int, reference: str) -> None:
    """Record the insurer's payment advice; the claim is then settled."""
    _require(db_path, claim_id, ClaimState.FINAL_APPROVED)
    if paid < 0:
        raise ValueError("a payment can't be negative")
    audit.log(db_path, claim_id, ACTOR, "payment_advice", {"paid": paid, "reference": reference})
    transition(db_path, claim_id, ClaimState.SETTLED, f"Paid ₹{paid:,} ({reference})")


def payment(db_path: Path | str, claim_id: str) -> dict[str, Any] | None:
    """The latest payment advice recorded for the claim, if any."""
    advice = [e["output"] for e in audit.events(db_path, claim_id) if e["action"] == "payment_advice"]
    return advice[-1] if advice else None


def reconcile(db_path: Path | str, claim_id: str, case: Case) -> RunResult:
    """Check the payment against the bill; close the claim if nothing is left to chase."""
    _require(db_path, claim_id, ClaimState.SETTLED)
    decision = next(
        m for m in reversed(exchange.thread(db_path, claim_id, Stage.DISCHARGE))
        if m.sender == Sender.INSURER and not isinstance(m, (Query, Deny))
    )
    result = call_agent(
        db_path, claim_id, reconcile_agent.AGENT, reconcile_agent.run,
        case, decision, _requested(db_path, claim_id), payment(db_path, claim_id)["paid"],
        masker=Masker.for_case(case),
    )
    if result is None:
        return RunResult(claim_id, claim_state(db_path, claim_id), {}, [], [reconcile_agent.AGENT])
    flags = [f"{reconcile_agent.AGENT}: {result.output.decision}"] if result.wants_human else []
    if result.data.to_chase == 0:
        transition(db_path, claim_id, ClaimState.CLOSED, "Paid in full under the policy: nothing to chase")
    return RunResult(claim_id, claim_state(db_path, claim_id), {reconcile_agent.AGENT: result}, flags, [])


def advise_patient(db_path: Path | str, claim_id: str, case: Case) -> AgentResult | None:
    """The patient's cost note and room advice, logged for staff to check before it is handed over."""
    state = claim_state(db_path, claim_id)  # the claim must exist
    basis = "final_bill" if state in (ClaimState.FINAL_APPROVED, ClaimState.SETTLED, ClaimState.CLOSED) or any(
        m.stage == Stage.DISCHARGE for m in exchange.thread(db_path, claim_id)) else "admission_estimate"
    return call_agent(db_path, claim_id, patient.AGENT, patient.run, case, basis, masker=Masker.for_case(case))


def _risk_check_and_queue(
    db_path: Path | str, claim_id: str, case: Case, results: dict[str, AgentResult], reason: str, stage: Stage = Stage.PREAUTH
) -> RunResult:
    transition(db_path, claim_id, ClaimState.RISK_CHECK, reason)
    risk = call_agent(
        db_path, claim_id, denial_risk.AGENT, denial_risk.run, case, _reading(db_path, case), stage, masker=Masker.for_case(case)
    )
    if risk is None:
        return _queue_for_person(db_path, claim_id, results, [denial_risk.AGENT], "Denial-risk agent failed twice")
    results = {**results, denial_risk.AGENT: risk}
    flags = [f"{name}: {r.output.decision}" for name, r in results.items() if r.wants_human]
    transition(db_path, claim_id, ClaimState.AWAITING_HUMAN, f"{reason}: needs approval")
    return RunResult(claim_id, ClaimState.AWAITING_HUMAN, results, flags, [])


def _queue_for_person(db_path: Path | str, claim_id: str, results: dict[str, AgentResult], failed: list[str], reason: str) -> RunResult:
    transition(db_path, claim_id, ClaimState.AWAITING_HUMAN, reason)
    return RunResult(claim_id, ClaimState.AWAITING_HUMAN, results, [reason], failed)


def _require(db_path: Path | str, claim_id: str, state: ClaimState) -> None:
    if (current := claim_state(db_path, claim_id)) != state:
        raise InvalidTransition(f"{claim_id} is {current}, not {state}")


def _approved(db_path: Path | str, claim_id: str) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT approved FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()["approved"] or 0
    finally:
        conn.close()


def _reading(db_path: Path | str, case: Case) -> Reading:
    """What the document reader extracted at admission, from the documents table."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT extracted_json FROM documents WHERE admission_id = ? AND extracted_json IS NOT NULL ORDER BY doc_id",
            (case.admission.admission_id,),
        ).fetchall()
    finally:
        conn.close()
    return Reading(documents=[ReadDocument.model_validate_json(row["extracted_json"]) for row in rows])


def _requested(db_path: Path | str, claim_id: str) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT requested FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()["requested"]
    finally:
        conn.close()


def _save_approved(db_path: Path | str, claim_id: str, amount: int) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE claims SET approved = ? WHERE claim_id = ?", (amount, claim_id))
    finally:
        conn.close()


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
