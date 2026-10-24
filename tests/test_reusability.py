"""Refinement of phases 4, 8 and 10: the same workflow under other policies, insurers and data sources."""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.adapters.billing_import import import_rows, parse
from backend.agents.approval import Decision, decide
from backend.agents.hospital import policy
from backend.agents.orchestrator import run_admission
from backend.api.main import create_app
from backend.exchange import messages as exchange
from backend.models import train_cost, train_denial
from backend.negotiation import play_round
from backend.policy_config import load_all, with_policy
from backend.schemas import Case, ClaimState, Document, DocumentType
from data.generator import tpa_profiles

SAMPLE = Path("fixtures/imports/sample_billing.csv")


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("disabled in tests")))
    monkeypatch.setenv("CLAIMPILOT_IMPORTS", str(tmp_path / "imports"))


def ramesh_with_xray() -> Case:
    case = Case.model_validate_json(Path("fixtures/case2.json").read_text(encoding="utf-8"))
    xray = Document(doc_id="ADM-0002-D6", type=DocumentType.XRAY_REPORT, file_path="fixtures/remediation/ADM-0002-xray-report.pdf")
    return case.model_copy(update={"documents": [*case.documents, xray]})


def first_decision(db: Path, case: Case):
    claim_id = run_admission(case, db).claim_id
    decide(db, claim_id, Decision(decision="approve", officer="Billing Officer"))
    play_round(db, claim_id, case)
    return exchange.thread(db, claim_id)[-1]


def test_the_same_workflow_runs_under_two_policy_configurations(tmp_path: Path) -> None:
    """No agent code changes: only the configuration differs, and so do the cited clauses and amounts."""
    under_a = first_decision(tmp_path / "a.db", with_policy(ramesh_with_xray(), "demo-a-health-secure"))
    under_b = first_decision(tmp_path / "b.db", with_policy(ramesh_with_xray(), "demo-b-value-plan"))
    assert under_a.approved_amount == 185_800  # 1% cap with proportionate cut (the Ramesh regression)
    assert under_b.approved_amount == 157_500  # fixed cap, knee sub-limit and 10% co-pay
    assert {d.clause for d in under_a.deductions} == {"Annexure II", "4.2"}
    assert {d.clause for d in under_b.deductions} == {"Annexure", "3.1", "3.6", "7.2"}


def test_extracted_policy_rules_need_a_person_before_use() -> None:
    extracted = [c for c in load_all().values() if c.source == "extracted"]
    assert extracted and not any(c.verified for c in extracted)
    result = policy.run("CLM-T", with_policy(ramesh_with_xray(), extracted[0].config_id))
    assert result.wants_human and result.data.unverified_rules


def test_the_simulator_can_switch_to_held_out_habits_without_code_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not tpa_profiles.profile_for("TPA-B").requires_xray_for_ortho
    with tpa_profiles.using("held_out"):
        assert tpa_profiles.profile_for("TPA-B").requires_xray_for_ortho
    monkeypatch.setenv("CLAIMPILOT_SIMULATOR", "held_out")
    assert tpa_profiles.profile_for("TPA-C").denies_without_admission_reason


def _imports(folder: str) -> set[str]:
    names = set()
    for path in Path(folder).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
            elif isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
    return names


@pytest.mark.parametrize("folder", ["backend/agents/hospital", "backend/evidence", "backend/models", "backend/adapters"])
def test_hospital_side_never_sees_hidden_profiles_or_labels(folder: str) -> None:
    names = _imports(folder)
    assert "data.generator.tpa_profiles" not in names and "data.generator.outcomes" not in names
    assert not any(n.startswith("backend.agents.insurer") or n.startswith("backend.bench") for n in names)
    for path in Path(folder).rglob("*.py"):
        assert "annotations.json" not in path.read_text(encoding="utf-8")


# ---- Billing import ------------------------------------------------------------------------------


def test_import_brings_in_cases_once(tmp_path: Path) -> None:
    rows = parse(SAMPLE.read_text(encoding="utf-8"), "csv")
    first = import_rows(rows, tmp_path)
    assert len(first["imported"]) == 2 and first["errors"] == []
    again = import_rows(rows, tmp_path)
    assert again["imported"] == [] and sorted(again["duplicates"]) == sorted(first["imported"])


def test_import_errors_say_which_row_and_what_to_fix(tmp_path: Path) -> None:
    rows = parse(SAMPLE.read_text(encoding="utf-8"), "csv")
    rows[2]["amount"] = "12k"
    rows[0]["admit_date"] = "02/10/2026"
    result = import_rows(rows, tmp_path)
    assert result["imported"] == [] and not list(tmp_path.glob("*.json"))  # nothing half-imported
    problems = {(e["row"], e["column"]) for e in result["errors"]}
    assert (4, "amount") in problems and (2, "admit_date") in problems
    assert all(e["fix"] for e in result["errors"])


def test_an_imported_admission_runs_through_the_api(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "claims.db")) as client:
        response = client.post("/api/imports", json={"format": "csv", "content": SAMPLE.read_text(encoding="utf-8")})
        assert response.status_code == 200
        case_id = response.json()["imported"][0]
        assert case_id in {c["case_id"] for c in client.get("/api/cases").json()}
        assert client.post("/api/imports", json={"format": "csv", "content": SAMPLE.read_text(encoding="utf-8")}).json()["duplicates"]
        claim_id = client.post("/api/cases/start", json={"case_id": case_id}).json()["claim_id"]
        client.app.state.runner.wait(claim_id)
        detail = client.get(f"/api/claims/{claim_id}").json()
        assert detail["state"] == ClaimState.AWAITING_HUMAN
        assert any(t["kind"] == "attach" for t in detail["tasks"])  # no records came with the export
        configs = client.get("/api/policy-configs").json()
        assert {"demo-a-health-secure", "demo-b-value-plan"} <= {c["config_id"] for c in configs}
