"""The simulated TPA: its own LangGraph, answering the hospital through the claim exchange (design section 5).

    medical reviewer + fraud screen (in parallel) -> adjudicator -> query writer (only for a query)

The insurer knows only what is on the exchange: the claim file sent with SUBMIT, the documents
attached so far, and the hospital's responses and appeals. It never imports hospital code. Each
agent's output is checked against AgentOutput and written to the audit log, like the hospital's.
"""

from functools import cache
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.base import AgentResult
from backend.agents.insurer import adjudicator, fraud, medical_review, query_writer
from backend.agents.insurer.adjudicator import Ruling
from backend.db import audit
from backend.evidence.clinical import Assessment, assess_admission_necessity, check_citations
from backend.exchange import messages as exchange
from backend.schemas import (
    AgentOutput,
    Appeal,
    Approve,
    Case,
    ClaimMessage,
    Deny,
    Document,
    Final,
    MessageType,
    PartialApprove,
    Query,
    Response,
    Sender,
    Stage,
    Submit,
)


class _Review(TypedDict):
    db_path: str
    claim_id: str
    case: Case
    stage: Stage
    requested: int
    round: int
    attached: list[Document]
    necessity: Assessment | None
    approved_claims: list[str]
    results: Annotated[dict[str, AgentResult], lambda a, b: {**a, **b}]


def review(db_path: Path | str, claim_id: str) -> ClaimMessage:
    """Answer the hospital's latest message on the claim and post the answer to the exchange."""
    last = exchange.latest(db_path, claim_id)
    if last is None or last.sender != Sender.HOSPITAL:
        raise exchange.ProtocolError(f"{claim_id}: no hospital message waiting for an answer")
    stage = exchange.thread(db_path, claim_id, last.stage)
    submit = next(m for m in stage if isinstance(m, Submit))
    case = Case.model_validate(submit.package["case"])

    sent_ids = {doc_id for m in stage if isinstance(m, (Submit, Response)) for doc_id in m.documents}
    cited_ids = [doc_id for m in stage if isinstance(m, (Appeal, Response)) for doc_id in _cited(m)]
    final = _graph().invoke({
        "db_path": str(db_path),
        "claim_id": claim_id,
        "case": case,
        "stage": last.stage,
        "requested": submit.requested_amount,
        "round": last.round,
        "attached": [doc for doc in case.documents if doc.doc_id in sent_ids],
        "necessity": _check_cited(case, cited_ids, sent_ids),
        "approved_claims": _approved_elsewhere(db_path, claim_id, case.admission.admission_id),
        "results": {},
    })
    results = final["results"]
    reply = _reply(claim_id, last, results[adjudicator.AGENT].data, results.get(query_writer.AGENT))
    exchange.post(db_path, reply)
    return reply


def _check_cited(case: Case, cited: list[str], sent: set[str]) -> Assessment | None:
    """Check the records the hospital cited: real, this admission's, sent to us, and saying what is claimed."""
    if not cited:
        return None
    valid, problems = check_citations(cited, case.documents, case.admission.admission_id, sent)
    return assess_admission_necessity(valid, problems=problems)


def _cited(message: Appeal | Response) -> list[str]:
    return message.cited_documents if isinstance(message, Appeal) else message.documents


def _reply(claim_id: str, last: ClaimMessage, ruling: Ruling, query: AgentResult | None) -> ClaimMessage:
    common = {"claim_id": claim_id, "stage": last.stage, "round": last.round}
    if ruling.kind == "query":
        return Query(**common, missing=query.data.missing, reason=query.data.reason)
    if ruling.final:
        decision = {"approve": MessageType.APPROVE, "partial": MessageType.PARTIAL_APPROVE, "deny": MessageType.DENY}[ruling.kind]
        return Final(**common, decision=decision, approved_amount=ruling.approved_amount, deductions=ruling.deductions)
    if ruling.kind == "deny":
        return Deny(**common, reason=ruling.reason, clause=ruling.clause)
    if ruling.kind == "partial":
        return PartialApprove(**common, approved_amount=ruling.approved_amount, deductions=ruling.deductions)
    return Approve(**common, approved_amount=ruling.approved_amount, deductions=ruling.deductions)


def _approved_elsewhere(db_path: Path | str, claim_id: str, admission_id: str) -> list[str]:
    """Other claims on the same admission that this insurer has already approved."""
    others = {s.claim_id for s in exchange.submissions(db_path) if s.claim_id != claim_id and s.package["case"]["admission"]["admission_id"] == admission_id}
    return sorted(other for other in others if any(_approves(m) for m in exchange.thread(db_path, other)))


def _approves(message: ClaimMessage) -> bool:
    return isinstance(message, (Approve, PartialApprove)) or (isinstance(message, Final) and message.decision != MessageType.DENY)


# ---- Graph ----------------------------------------------------------------------------------


def _step(state: _Review, name: str, run: Callable[..., AgentResult], *args: Any) -> dict[str, Any]:
    result = run(state["claim_id"], *args)
    AgentOutput.model_validate(result.output.model_dump())  # the insurer is held to the same contract
    audit.log(
        state["db_path"], state["claim_id"], name, "agent_output",
        {"output": result.output.model_dump(mode="json"), "data": result.data.model_dump(mode="json")},
        inputs=args,
    )
    return {"results": {name: result}}


def _medical_review(state: _Review) -> dict[str, Any]:
    return _step(state, medical_review.AGENT, medical_review.run, state["case"], state["attached"], state["necessity"], state["stage"])


def _fraud_screen(state: _Review) -> dict[str, Any]:
    return _step(state, fraud.AGENT, fraud.run, state["case"], state["approved_claims"])


def _adjudicate(state: _Review) -> dict[str, Any]:
    results = state["results"]
    return _step(
        state, adjudicator.AGENT, adjudicator.run,
        state["case"], state["requested"], results[medical_review.AGENT].data, results[fraud.AGENT].data, state["round"],
    )


def _write_query(state: _Review) -> dict[str, Any]:
    return _step(state, query_writer.AGENT, query_writer.run, state["results"][adjudicator.AGENT].data.gaps)


@cache
def _graph():
    graph = StateGraph(_Review)
    graph.add_node("medical_reviewer", _medical_review)
    graph.add_node("fraud_screen", _fraud_screen)
    graph.add_node("adjudicator", _adjudicate)
    graph.add_node("query_writer", _write_query)
    graph.add_edge(START, "medical_reviewer")  # the two reviews don't need each other
    graph.add_edge(START, "fraud_screen")
    graph.add_edge(["medical_reviewer", "fraud_screen"], "adjudicator")
    graph.add_conditional_edges(
        "adjudicator",
        lambda state: "query_writer" if state["results"][adjudicator.AGENT].data.kind == "query" else END,
        ["query_writer", END],
    )
    graph.add_edge("query_writer", END)
    return graph.compile()
