"""Phase 8: the simulated insurer, the claim exchange, and negotiation up to three rounds."""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

from backend import llm
from backend.agents import orchestrator
from backend.agents.approval import Decision, decide
from backend.agents.orchestrator import InvalidTransition, pending_draft, run_admission
from backend.db import audit
from backend.db.db import connect
from backend.exchange import messages as exchange
from backend.models import train_cost, train_denial
from backend.negotiation import play_round
from backend.schemas import (
    MAX_ROUNDS,
    AgentOutput,
    Appeal,
    Approve,
    Case,
    ClaimState,
    Deny,
    Document,
    DocumentType,
    Final,
    MessageType,
    PartialApprove,
    Stage,
    Submit,
)
from data.generator.render import render_pdf

OFFICER = "Billing Officer"
INSURER_AGENTS = ["tpa_medical_reviewer", "tpa_fraud_screen", "tpa_adjudicator"]


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests don't depend on a running model; agents fall back to their templates."""

    def unavailable(*args, **kwargs) -> str:
        raise llm.LLMUnavailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


def approve() -> Decision:
    return Decision(decision="approve", officer=OFFICER)


def negotiate(db: Path, case: Case, max_gates: int = MAX_ROUNDS) -> str:
    """Admit the case, then approve every drafted message, as a billing officer would."""
    result = run_admission(case, db)
    for _ in range(max_gates):
        if result.state != ClaimState.AWAITING_HUMAN or pending_draft(db, result.claim_id) is None:
            break
        decide(db, result.claim_id, approve())
        result = play_round(db, result.claim_id, case)
    return result.claim_id


def to_discharge(db: Path, case: Case) -> str:
    """Admit, approve the pre-auth, and send the final package: Case 3's dispute is at discharge."""
    claim_id = negotiate(db, case)
    assert orchestrator.claim_state(db, claim_id) == ClaimState.APPROVED
    orchestrator.next_stage(db, claim_id)
    orchestrator.discharge(db, claim_id, case)
    decide(db, claim_id, approve())
    play_round(db, claim_id, case)
    return claim_id


def with_xray(case: Case, tmp_path: Path) -> Case:
    """Case 2 with the fix the denial-risk agent suggested: the pre-op X-ray report attached."""
    xray = Document(
        doc_id="ADM-0002-D6",
        type=DocumentType.XRAY_REPORT,
        file_path=str(tmp_path / "ADM-0002-D6.pdf"),
        fields={"finding": "Severe medial joint-space narrowing with osteophytes, right knee"},
    )
    Path(xray.file_path).write_bytes(render_pdf(xray, case, None))
    return case.model_copy(update={"documents": [*case.documents, xray]})


def by_clause(deductions) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for d in deductions:
        totals[d.clause] += d.amount
    return dict(totals)


def claim_row(db: Path, claim_id: str):
    conn = connect(db)
    try:
        return conn.execute("SELECT state, requested, approved FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    finally:
        conn.close()


# ---- The three cases --------------------------------------------------------------------------


def test_case1_is_approved(db: Path) -> None:
    claim_id = negotiate(db, load("case1.json"))
    submit, answer = exchange.thread(db, claim_id)
    assert isinstance(submit, Submit) and isinstance(answer, Approve)
    assert answer.round == 1 and answer.deductions == []
    row = claim_row(db, claim_id)
    assert row["state"] == ClaimState.APPROVED
    assert answer.approved_amount == row["approved"] == min(98_000, row["requested"])  # never more than requested


def test_case2_after_fixes_is_approved_with_cited_deductions(db: Path, tmp_path: Path) -> None:
    claim_id = negotiate(db, with_xray(load("case2.json"), tmp_path))
    messages = exchange.thread(db, claim_id)
    assert [m.type for m in messages] == [MessageType.SUBMIT, MessageType.PARTIAL_APPROVE]
    answer = messages[-1]
    assert answer.approved_amount == 185_800  # Ramesh's worked example, from the rules engine
    assert by_clause(answer.deductions) == {"Annexure II": 7_000, "4.2": 47_200}
    assert all(d.clause for d in answer.deductions)
    # The policy supports every deduction, so the hospital accepts and sends nothing more.
    outputs = [e["output"]["output"] for e in audit.events(db, claim_id) if e["actor"] == "appeal" and e["action"] == "agent_output"]
    assert [o["decision"] for o in outputs] == ["accept_decision"]
    assert claim_row(db, claim_id)["state"] == ClaimState.PARTIALLY_APPROVED
    assert pending_draft(db, claim_id) is None


def test_case3_is_partly_denied_appealed_and_accepted_in_round_two(db: Path) -> None:
    case = load("case3.json")
    claim_id = to_discharge(db, case)
    decide(db, claim_id, approve())
    play_round(db, claim_id, case)
    submit, cut, appeal, answer = exchange.thread(db, claim_id, Stage.DISCHARGE)
    assert isinstance(cut, PartialApprove) and cut.round == 1
    assert "Medical necessity: reason for admission not documented" in by_clause(cut.deductions)
    assert isinstance(appeal, Appeal) and appeal.round == 2
    assert set(appeal.cited_documents) <= {doc.doc_id for doc in case.documents}  # only records the claim has
    assert "ADM-0003-D1" in appeal.cited_documents
    assert isinstance(answer, Approve) and answer.round == 2
    assert answer.approved_amount > cut.approved_amount
    assert by_clause(answer.deductions) == {"6.1": 4_250}  # only the co-pay is left
    assert claim_row(db, claim_id)["state"] == ClaimState.APPROVED
    assert [m.type for m in exchange.thread(db, claim_id, Stage.PREAUTH)] == [MessageType.SUBMIT, MessageType.APPROVE]


def test_claim_forced_past_three_rounds_lands_in_the_human_queue(db: Path) -> None:
    case = load("case2.json")  # no X-ray report, and none to find: TPA-A keeps asking
    claim_id = negotiate(db, case, max_gates=10)
    messages = exchange.thread(db, claim_id)
    assert [m.round for m in messages] == [1, 1, 2, 2, 3, 3]
    assert isinstance(messages[-1], Final) and messages[-1].decision == MessageType.DENY
    assert claim_row(db, claim_id)["state"] == ClaimState.AWAITING_HUMAN
    reasons = [e["output"]["reason"] for e in audit.events(db, claim_id) if e["action"] == "transition"]
    assert reasons[-1] == "3 rounds used without full approval: a person decides"

    with pytest.raises(ValueError, match="nothing drafted"):
        decide(db, claim_id, approve())  # there is no fourth round to send
    assert decide(db, claim_id, Decision(decision="accept", officer=OFFICER)) == ClaimState.DENIED


# ---- Gates and protocol ---------------------------------------------------------------------


def test_nothing_reaches_the_insurer_without_approval(db: Path) -> None:
    case = load("case1.json")
    claim_id = run_admission(case, db).claim_id
    with pytest.raises(InvalidTransition):
        orchestrator.send(db, claim_id, case)
    submit = Submit(claim_id=claim_id, stage=Stage.PREAUTH, round=1, package={}, documents=[], requested_amount=1)
    with pytest.raises(exchange.ProtocolError, match="SUBMITTED"):
        exchange.post(db, submit)
    assert exchange.thread(db, claim_id) == []


def test_insurer_answers_each_round_once(db: Path) -> None:
    claim_id = negotiate(db, load("case1.json"))
    with pytest.raises(exchange.ProtocolError):
        exchange.post(db, Deny(claim_id=claim_id, stage=Stage.PREAUTH, round=1, reason="again", clause="x"))


def test_rejected_appeal_goes_back_to_the_insurers_decision(db: Path) -> None:
    case = load("case3.json")
    claim_id = to_discharge(db, case)
    assert pending_draft(db, claim_id)["message"]["type"] == "APPEAL"
    state = decide(db, claim_id, Decision(decision="reject", officer=OFFICER, reason="Get a doctor's note first"))
    assert state == ClaimState.PARTIALLY_APPROVED
    assert pending_draft(db, claim_id) is None
    assert len(exchange.thread(db, claim_id, Stage.DISCHARGE)) == 2  # the appeal was never sent


def test_edited_appeal_text_is_what_the_insurer_gets(db: Path) -> None:
    case = load("case3.json")
    claim_id = to_discharge(db, case)
    text = "Admission note ADM-0003-D1 records BP 92/60 and pulse 112: the patient was dehydrated and needed IV fluids."
    decide(db, claim_id, Decision(decision="edit", officer=OFFICER, edits={"justification": text}))
    play_round(db, claim_id, case)
    appeal = next(m for m in exchange.thread(db, claim_id) if isinstance(m, Appeal))
    assert appeal.argument == text


def test_fraud_screen_denies_a_duplicate_claim(db: Path) -> None:
    case = load("case1.json")
    negotiate(db, case)
    second = negotiate(db, case)
    answer = exchange.thread(db, second)[-1]
    assert isinstance(answer, Deny) and answer.clause == "Duplicate claim"
    # A whole-claim denial isn't appealed automatically: it waits for a person, with nothing drafted.
    assert claim_row(db, second)["state"] == ClaimState.AWAITING_HUMAN
    assert pending_draft(db, second) is None


def test_every_insurer_output_validates_and_has_sources(db: Path) -> None:
    claim_id = to_discharge(db, load("case3.json"))
    events = [e for e in audit.events(db, claim_id) if e["action"] == "agent_output" and e["actor"].startswith("tpa_")]
    assert sorted({e["actor"] for e in events}) == sorted(INSURER_AGENTS)
    for event in events:
        output = AgentOutput.model_validate(event["output"]["output"])
        assert output.findings and all(f.source.strip() for f in output.findings)


# ---- Boundaries -------------------------------------------------------------------------------


def _imports(folder: str) -> dict[str, set[str]]:
    found = {}
    for path in Path(folder).glob("*.py"):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                names |= {node.module, *(f"{node.module}.{a.name}" for a in node.names)}
            elif isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
        found[str(path)] = names
    return found


def test_hospital_and_insurer_never_import_each_other() -> None:
    for path, names in [*_imports("backend/agents/hospital").items(), *_imports("backend/agents").items()]:
        assert not any(n.startswith("backend.agents.insurer") for n in names), f"{path} imports the insurer"
        assert "data.generator.tpa_profiles" not in names, f"{path} reads the TPAs' hidden profiles"
    for path, names in _imports("backend/agents/insurer").items():
        assert not any(n.startswith(("backend.agents.hospital", "backend.agents.orchestrator")) for n in names), f"{path} imports the hospital"
