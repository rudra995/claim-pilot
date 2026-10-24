"""Refinement of phase 12: the benchmark harness itself (a smoke run on the demo cases)."""

from pathlib import Path

import pytest

from backend import llm
from backend.bench.run import MODES, _tuning_cases, run_case, summarise
from backend.models import train_cost, train_denial


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


def test_each_mode_runs_the_same_cases_and_basic_waits_to_be_asked(tmp_path: Path) -> None:
    case2 = _tuning_cases(tmp_path)[1]  # Ramesh: X-ray report held in records but not attached
    rows = {mode: run_case(tmp_path / f"{mode}.db", case2, mode) for mode in MODES}
    assert rows["basic"]["stages"]["preauth"]["first_answer"] == "QUERY"  # it didn't look, so the insurer asked
    assert rows["basic"]["retrievals"] == ["xray_report"]  # ...and the same record was then fetched for it
    for mode in ("rules", "claimpilot"):
        assert rows[mode]["stages"]["preauth"]["first_answer"] == "PARTIAL_APPROVE"  # fetched before sending
        assert "missing_xray_report" in rows[mode]["found"]
    assert all(row["stages"]["preauth"]["approved"] == 185_800 for row in rows.values())
    summary = summarise([rows["claimpilot"]])
    assert summary["cases"] == 1 and summary["preauth_query_free_first_response"] == 1.0
