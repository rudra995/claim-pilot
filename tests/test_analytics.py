"""Refinement of phase 12: the dashboard's analytics, measured from what the system recorded."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.api.analytics import benchmark_summary
from backend.api.main import create_app
from backend.models import train_cost, train_denial

OFFICER = "Billing Officer"


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("disabled in tests")))
    with TestClient(create_app(tmp_path / "claims.db")) as client:
        assert client.post("/api/auth/login", json={"username": "officer@claimpilot.demo", "password": "OfficerDemo!2026"}).status_code == 200
        yield client


def run(client: TestClient, case_id: str) -> str:
    claim_id = client.post("/api/cases/start", json={"case_id": case_id}).json()["claim_id"]
    client.app.state.runner.wait(claim_id)
    draft = client.get(f"/api/claims/{claim_id}").json()["pending_draft"]
    client.post(f"/api/claims/{claim_id}/decision", json={"officer": OFFICER, "decision": "approve", "draft_hash": draft["hash"]})
    client.app.state.runner.wait(claim_id)
    return claim_id


def test_an_empty_desk_has_no_rates_rather_than_zero(client: TestClient) -> None:
    a = client.get("/api/analytics").json()
    assert a["claims"] == 0 and a["kpis"]["approved_in_full_first_time"] is None


def test_part_approvals_are_never_counted_as_approved_first_time(client: TestClient, tmp_path: Path) -> None:
    run(client, "case1")  # approved in full
    xray = Path("fixtures/remediation/ADM-0002-xray-report.pdf")
    ramesh = client.post("/api/cases/start", json={"case_id": "case2"}).json()["claim_id"]
    client.app.state.runner.wait(ramesh)
    import base64

    client.post(f"/api/claims/{ramesh}/documents", json={"type": "xray_report", "filename": "x.pdf", "content_base64": base64.b64encode(xray.read_bytes()).decode(), "officer": OFFICER})
    client.post(f"/api/claims/{ramesh}/recheck", json={"reason": "X-ray attached"})
    client.app.state.runner.wait(ramesh)
    draft = client.get(f"/api/claims/{ramesh}").json()["pending_draft"]
    client.post(f"/api/claims/{ramesh}/decision", json={"officer": OFFICER, "decision": "approve", "draft_hash": draft["hash"]})
    client.app.state.runner.wait(ramesh)

    a = client.get("/api/analytics").json()
    assert a["kpis"]["approved_in_full_first_time"] == 0.5 and a["kpis"]["part_approved_first_time"] == 0.5
    assert client.get("/api/dashboard").json()["first_pass_rate"] == 0.5  # the old endpoint now agrees
    pre = next(r for r in a["first_answers"] if r["label"] == "Pre-auth")
    assert (pre["APPROVE"], pre["PARTIAL_APPROVE"], pre["QUERY"]) == (1, 1, 0)
    assert {r["label"]: r["value"] for r in a["deductions_by_clause"]} == {"Clause 4.2": 47_200, "Annexure II": 7_000}
    assert a["deductions_split"]["policy_backed"] == 54_200 and a["deductions_split"]["disputed"] == 0
    claims = client.get("/api/claims").json()
    assert a["kpis"]["approved_total"] == sum(c["approved"] or 0 for c in claims)  # checked here; the API used the engine
    assert a["oversight"]["documents_attached"] == 1 and a["oversight"]["rechecks"] == 1
    assert sum(b["value"] for b in a["risk_histogram"]) == 2
    assert sum(p["value"] for p in a["pipeline"]) == 2


def test_benchmark_panel_is_labelled_as_synthetic() -> None:
    summary = benchmark_summary()
    if summary is None:
        pytest.skip("run python -m backend.bench.run first")
    assert "synthetic" in summary["label"] and summary["modes"] == ["basic", "rules", "claimpilot"]
    assert all(set(m["values"]) == set(summary["modes"]) for m in summary["metrics"])
