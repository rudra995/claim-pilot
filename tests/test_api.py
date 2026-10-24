"""Phase 10: the API and the live trace over a WebSocket."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.agents.orchestrator import ADMISSION_AGENTS
from backend.api.main import create_app
from backend.db import audit
from backend.models import train_cost, train_denial
from backend.schemas import ClaimState

OFFICER = "Billing Officer"
MAX_TRACE_MESSAGES = 500  # a guard so a broken stream fails the test instead of hanging it


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args, **kwargs) -> str:
        raise llm.LLMUnavailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "claims.db"


@pytest.fixture
def client(db: Path):
    with TestClient(create_app(db)) as client:
        assert client.post("/api/auth/login", json={"username": "officer@claimpilot.demo", "password": "OfficerDemo!2026"}).status_code == 200
        yield client


def wait(client: TestClient, claim_id: str) -> dict:
    client.app.state.runner.wait(claim_id)
    return client.get(f"/api/claims/{claim_id}").json()


def start(client: TestClient, case_id: str) -> str:
    response = client.post("/api/cases/start", json={"case_id": case_id})
    assert response.status_code == 202
    return response.json()["claim_id"]


def decide(client: TestClient, claim_id: str, **body) -> dict:
    response = client.post(f"/api/claims/{claim_id}/decision", json={"officer": OFFICER, **body})
    assert response.status_code == 200, response.text
    return response.json()


# ---- Live trace ------------------------------------------------------------------------------


def test_starting_case1_streams_every_step(client: TestClient, db: Path) -> None:
    with client.websocket_connect("/ws/trace?after=-1") as ws:  # connected before anything happens
        claim_id = start(client, "case1")
        streamed = []
        for _ in range(MAX_TRACE_MESSAGES):
            message = ws.receive_json()
            if message["type"] != "event" or message["claim_id"] != claim_id:
                continue
            streamed.append(message)
            if message["action"] == "transition" and message["output"]["to"] == ClaimState.AWAITING_HUMAN:
                break

    agents = [m["actor"] for m in streamed if m["action"] == "agent_output"]
    assert sorted(agents) == sorted(ADMISSION_AGENTS)
    assert agents.index("document_reader") < agents.index("clinical_coder") < agents.index("preauth") < agents.index("denial_risk")
    assert [m["output"]["to"] for m in streamed if m["action"] == "transition"] == ["PREAUTH_DRAFT", "RISK_CHECK", "AWAITING_HUMAN"]
    assert all(m["summary"] for m in streamed)
    # Nothing was left out: the stream is the claim's audit log, event for event.
    client.app.state.runner.wait(claim_id)
    logged = [e["event_id"] for e in audit.events(db, claim_id)]
    assert [m["event_id"] for m in streamed] == logged[: len(streamed)]


def test_trace_for_one_claim_replays_from_the_start(client: TestClient) -> None:
    claim_id = start(client, "case3")
    wait(client, claim_id)
    with client.websocket_connect(f"/ws/trace?claim_id={claim_id}") as ws:
        first = ws.receive_json()
    assert first["claim_id"] == claim_id and first["action"] == "open_claim"


# ---- Approval actions ------------------------------------------------------------------------


def test_approve_through_the_api_sends_the_claim_and_moves_on(client: TestClient) -> None:
    claim_id = start(client, "case1")
    detail = wait(client, claim_id)
    assert detail["state"] == ClaimState.AWAITING_HUMAN
    assert detail["actions"][:3] == ["approve", "edit", "reject"]
    assert detail["pending_draft"]["message"]["type"] == "SUBMIT"

    assert decide(client, claim_id, decision="approve")["job"] == "negotiation"
    detail = wait(client, claim_id)
    assert [m["type"] for m in detail["thread"]] == ["SUBMIT", "APPROVE"]
    assert "case" not in detail["thread"][0]["package"]
    assert detail["state"] == ClaimState.IN_TREATMENT  # pre-auth approved: treatment goes on
    assert detail["approved"] == detail["thread"][1]["approved_amount"]
    assert any(e["action"] == "human_decision" and e["actor"] == f"human:{OFFICER}" for e in detail["timeline"])


def test_reject_through_the_api_sends_nothing(client: TestClient) -> None:
    claim_id = start(client, "case2")
    wait(client, claim_id)
    result = decide(client, claim_id, decision="reject", reason="Attach the pre-op X-ray report")
    assert result["state"] == ClaimState.PREAUTH_DRAFT and result["job"] is None
    assert client.get(f"/api/claims/{claim_id}/thread").json() == []


def test_bad_decisions_are_refused(client: TestClient) -> None:
    claim_id = start(client, "case1")
    wait(client, claim_id)
    no_reason = client.post(f"/api/claims/{claim_id}/decision", json={"decision": "reject", "officer": OFFICER})
    assert no_reason.status_code == 422
    assert client.post(f"/api/claims/{claim_id}/decision", json={"decision": "accept", "officer": OFFICER}).status_code == 409
    decide(client, claim_id, decision="approve")
    wait(client, claim_id)
    again = client.post(f"/api/claims/{claim_id}/decision", json={"decision": "approve", "officer": OFFICER})
    assert again.status_code == 409  # no longer waiting for a person
    assert client.get("/api/claims/CLM-NOPE").status_code == 404


def test_case3_appeal_is_approved_through_the_api(client: TestClient) -> None:
    claim_id = start(client, "case3")
    wait(client, claim_id)
    decide(client, claim_id, decision="approve")
    assert wait(client, claim_id)["state"] == ClaimState.IN_TREATMENT  # the pre-auth had nothing to cut
    assert client.post(f"/api/claims/{claim_id}/discharge").status_code == 202
    wait(client, claim_id)
    decide(client, claim_id, decision="approve")
    detail = wait(client, claim_id)
    assert detail["state"] == ClaimState.AWAITING_HUMAN and detail["pending_draft"]["message"]["type"] == "APPEAL"
    assert "denial_risk: fix_before_submit" in detail["flags"]
    decide(client, claim_id, decision="approve")
    detail = wait(client, claim_id)
    discharge = [m["type"] for m in detail["thread"] if m["stage"] == "discharge"]
    assert discharge == ["SUBMIT", "PARTIAL_APPROVE", "APPEAL", "APPROVE"]
    assert detail["state"] == ClaimState.FINAL_APPROVED


# ---- The rest of the stay, and the screens' reads -------------------------------------------


def test_case1_runs_to_closed_through_the_api(client: TestClient) -> None:
    claim_id = start(client, "case1")
    wait(client, claim_id)
    decide(client, claim_id, decision="approve")
    wait(client, claim_id)

    assert client.post(f"/api/claims/{claim_id}/discharge").status_code == 202
    detail = wait(client, claim_id)
    assert detail["pending_draft"]["message"]["stage"] == "discharge"
    decide(client, claim_id, decision="approve")
    detail = wait(client, claim_id)
    assert detail["state"] == ClaimState.FINAL_APPROVED and detail["actions"][0] == "payment"

    paid = client.post(f"/api/claims/{claim_id}/payment", json={"paid": detail["approved"], "reference": "UTR-0001"})
    assert paid.status_code == 202
    assert wait(client, claim_id)["state"] == ClaimState.CLOSED


def test_bill_past_the_approval_asks_for_an_enhancement(client: TestClient) -> None:
    claim_id = start(client, "case1")
    wait(client, claim_id)
    decide(client, claim_id, decision="approve")
    approved = wait(client, claim_id)["approved"]
    line = {"line_id": "ADM-0001-L7", "category": "icu", "description": "ICU, 2 days", "quantity": 2, "amount": 60_000}
    assert client.post(f"/api/claims/{claim_id}/bill", json={"lines": [line]}).status_code == 202
    detail = wait(client, claim_id)
    assert detail["state"] == ClaimState.AWAITING_HUMAN
    draft = detail["pending_draft"]["message"]
    assert draft["stage"] == "enhancement" and draft["requested_amount"] > approved
    assert any(l["line_id"] == "ADM-0001-L7" for l in detail["case"]["bill_lines"])


def test_patient_note_gives_case2_room_advice(client: TestClient) -> None:
    claim_id = start(client, "case2")
    wait(client, claim_id)
    note = client.post(f"/api/claims/{claim_id}/patient-note").json()["note"]
    assert note["recommended"]["saving"] == 47_200


def test_lists_queue_and_dashboard(client: TestClient) -> None:
    ids = [start(client, case_id) for case_id in ("case1", "case2")]
    for claim_id in ids:
        wait(client, claim_id)
    cases = client.get("/api/cases").json()
    assert {"case1", "case2", "case3"} <= {c["case_id"] for c in cases}
    assert sum(not c["demo"] for c in cases) >= 10  # preset patients, mostly clean claims
    claims = client.get("/api/claims").json()
    assert [c["claim_id"] for c in claims] == ids and all(c["title"] for c in claims)
    queue = client.get("/api/queue").json()
    assert {c["claim_id"] for c in queue} == set(ids)
    assert all(c["risk"] is not None for c in queue)

    decide(client, ids[0], decision="approve")
    wait(client, ids[0])
    numbers = client.get("/api/dashboard").json()
    assert numbers["claims"] == 2 and numbers["awaiting_human"] == 1
    assert numbers["first_pass_rate"] == 1.0
    assert numbers["approved_total"] > 0


def test_claim_opened_by_the_cli_can_be_viewed(client: TestClient, db: Path) -> None:
    """run_case opens claims without the API's case file; the API falls back to the fixture."""
    from backend.agents.orchestrator import run_admission
    from backend.api.store import fixture_cases

    claim_id = run_admission(fixture_cases()["case1"], db).claim_id
    detail = client.get(f"/api/claims/{claim_id}")
    assert detail.status_code == 200
    assert detail.json()["case_id"] == "case1" and detail.json()["title"]


def test_sent_back_preauth_can_be_redrafted(client: TestClient) -> None:
    claim_id = start(client, "case2")
    wait(client, claim_id)
    decide(client, claim_id, decision="reject", reason="Check the room first")
    assert "redraft" in client.get(f"/api/claims/{claim_id}").json()["actions"]
    assert client.post(f"/api/claims/{claim_id}/redraft").status_code == 202
    detail = wait(client, claim_id)
    assert detail["state"] == ClaimState.AWAITING_HUMAN
    assert detail["pending_draft"]["message"]["type"] == "SUBMIT"


def test_every_preset_patient_admits_cleanly(client: TestClient) -> None:
    """Each preset reaches the human gate with its documents read and a pre-auth drafted."""
    presets = [c["case_id"] for c in client.get("/api/cases").json() if not c["demo"]]
    for case_id in presets:
        claim_id = start(client, case_id)
        detail = wait(client, claim_id)
        assert detail["state"] == ClaimState.AWAITING_HUMAN, (case_id, detail["last_reason"], detail["errors"])
        assert detail["pending_draft"]["message"]["type"] == "SUBMIT", case_id
