"""Shared test setup."""

import pytest

from backend import llm


@pytest.fixture(autouse=True)
def fresh_llm_cooldown() -> None:
    """The LLM client pauses after a failure; each test starts with it ready."""
    llm._down.update(until=0.0, error="")
