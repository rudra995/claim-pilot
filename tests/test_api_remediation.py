"""Refinement of phase 10: the fix-and-recheck workflow and approval integrity through the API."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.api.main import create_app
from backend.models import train_cost, train_denial
from backend.schemas import ClaimState

OFFICER = "Billing Officer"
XRAY = Path("fixtures/remediation/ADM-0002-xray-report.pdf")


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("disabled in tests")))
    monkeypatch.setenv("CLAIMPILOT_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMPILOT_OFFICER_TOKENS", raising=False)


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(tmp_path / "claims.db")) as client:
        assert client.post("/api/auth/login", json={"username": "officer@claimpilot.demo", "password": "OfficerDemo!2026"}).status_code == 200
        yield client


def wait(client: TestClient, claim_id: str) -> dict:
    client.app.state.runner.wait(claim_id)
    return client.get(f"/api/claims/{claim_id}").json()


def start(client: TestClient, case_id: str) -> str:
    claim_id = client.post("/api/cases/start", json={"case_id": case_id}).json()["claim_id"]
    wait(client, claim_id)
    return claim_id


def upload(client: TestClient, claim_id: str, content: bytes, doc_type: str = "xray_report", **headers):
    body = {"type": doc_type, "filename": "xray.pdf", "content_base64": base64.b64encode(content).decode(), "officer": OFFICER}
    return client.post(f"/api/claims/{claim_id}/documents", json=body, headers=headers)


def test_attach_recheck_and_approve_the_new_version(client: TestClient) -> None:
    claim_id = start(client, "case2")
    before = client.get(f"/api/claims/{claim_id}").json()
    assert {"kind": "attach", "doc_type": "xray_report"}.items() <= before["tasks"][0].items()
    assert "attach" in before["actions"] and "recheck" in before["actions"]

    attached = upload(client, claim_id, XRAY.read_bytes())
    assert attached.status_code == 201 and not attached.json()["duplicate"]
    assert upload(client, claim_id, XRAY.read_bytes()).json()["duplicate"]  # same file again: nothing added
    assert client.post(f"/api/claims/{claim_id}/recheck", json={"reason": "X-ray attached"}).status_code == 202
    after = wait(client, claim_id)

    assert after["pending_draft"]["version"] == before["pending_draft"]["version"] + 1
    assert after["last_recheck"]["resolved"] == ["Missing xray report"]
    assert [d["status"] for d in after["drafts"]] == ["superseded", "pending"]
    assert after["attachments"][0]["doc_id"] == "ADM-0002-U1" and after["attachments"][0]["by"] == OFFICER
    assert not any(t["kind"] == "attach" for t in after["tasks"])

    stale = {"officer": OFFICER, "decision": "approve", "draft_hash": before["pending_draft"]["hash"]}
    assert client.post(f"/api/claims/{claim_id}/decision", json=stale).status_code == 409
    fresh = {"officer": OFFICER, "decision": "approve", "draft_hash": after["pending_draft"]["hash"]}
    assert client.post(f"/api/claims/{claim_id}/decision", json=fresh).json()["job"] == "negotiation"
    repeat = client.post(f"/api/claims/{claim_id}/decision", json=fresh)  # a double click
    assert repeat.status_code == 200 and repeat.json()["duplicate"]
    done = wait(client, claim_id)
    assert [m["type"] for m in done["thread"]] == ["SUBMIT", "PARTIAL_APPROVE"]  # sent once, no query
    assert done["thread"][1]["approved_amount"] == 185_800


def test_a_bad_upload_says_what_to_fix(client: TestClient) -> None:
    claim_id = start(client, "case2")
    response = upload(client, claim_id, b"hello")
    assert response.status_code == 422 and "Only PDF" in response.json()["detail"]
    bad = client.post(f"/api/claims/{claim_id}/documents", json={"type": "xray_report", "filename": "x.pdf", "content_base64": "!!!notbase64", "officer": OFFICER})
    assert bad.status_code == 422


def test_document_files_are_served_only_for_the_claims_own_documents(client: TestClient) -> None:
    claim_id = start(client, "case2")
    ok = client.get(f"/api/claims/{claim_id}/documents/ADM-0002-D1/file")
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/claims/{claim_id}/documents/ADM-0001-D1/file").status_code == 404


def test_officer_tokens_decide_who_is_acting(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    claim_id = start(client, "case1")
    monkeypatch.setenv("CLAIMPILOT_OFFICER_TOKENS", "Asha Rao=s3cret")
    body = {"officer": "Someone Else", "decision": "reject", "reason": "Check the bill"}
    assert client.post(f"/api/claims/{claim_id}/decision", json=body).status_code == 401
    response = client.post(f"/api/claims/{claim_id}/decision", json=body, headers={"X-Officer-Token": "s3cret"})
    assert response.status_code == 200 and response.json()["state"] == ClaimState.PREAUTH_DRAFT
    timeline = wait(client, claim_id)["timeline"]
    decision = next(e for e in timeline if e["action"] == "human_decision")
    assert decision["actor"] == "human:Asha Rao" and decision["output"]["identity"] == "token"


def test_readiness_tells_api_models_and_llm_apart(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIMPILOT_LLM", "off")
    ready = client.get("/api/ready").json()
    assert ready["api"] == "ok" and ready["models"] == {"cost": True, "denial": True}
    assert ready["llm"]["mode"] == "fallback" and "CLAIMPILOT_LLM=off" in ready["llm"]["reason"]


def test_a_claim_stopped_after_sending_resumes_without_sending_twice(client: TestClient, tmp_path: Path) -> None:
    from backend.agents import orchestrator
    from backend.agents.approval import Decision, decide
    from backend.api import store
    from backend.exchange import messages as exchange

    db = client.app.state.db_path
    claim_id = start(client, "case1")
    decide(db, claim_id, Decision(decision="approve", officer=OFFICER))
    orchestrator.send(db, claim_id, store.load(db, claim_id))  # ...and the process stopped here
    assert claim_id in client.get("/api/ready").json()["interrupted_claims"]
    assert client.post(f"/api/claims/{claim_id}/resume").status_code == 202
    done = wait(client, claim_id)
    assert [m["type"] for m in done["thread"]] == ["SUBMIT", "APPROVE"]
    assert done["state"] == ClaimState.IN_TREATMENT
    assert len(exchange.submissions(db)) == 1
