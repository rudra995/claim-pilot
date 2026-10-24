"""Refinement of phase 14: the core demo runs end to end from a clean state."""

import importlib.util
from pathlib import Path

import pytest

from backend.models import train_cost, train_denial


@pytest.fixture(scope="module", autouse=True)
def trained() -> None:
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


def test_the_demo_story_passes_every_step(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("demo_check", Path("scripts/demo_check.py"))
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    for name in ("CLAIMPILOT_LLM", "CLAIMPILOT_UPLOADS", "CLAIMPILOT_SIMULATOR"):
        monkeypatch.setenv(name, "placeholder")  # run_once sets its own values; monkeypatch restores ours after
    results = demo.run_once(record=False)
    assert [step for step, ok, _ in results if not ok] == []
    assert len(results) == 7
