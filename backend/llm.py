"""One small LLM client for the project: local Ollama first, Gemini as the fallback.

    OLLAMA_MODEL    local model (default qwen2.5-coder:7b)
    OLLAMA_URL      default http://localhost:11434
    GEMINI_API_KEY  enables the fallback
    GEMINI_MODEL    default gemini-2.5-flash

Every prompt is masked before it leaves (design section 9): phone, Aadhaar and PAN numbers always,
and the patient's name and ids too while an agent runs for a claim (`for_claim`). Inside a claim,
each call is written to the audit log with the masked prompt, so the log never holds the name.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.db import audit
from backend.privacy import Masker


class LLMUnavailable(RuntimeError):
    """Neither Ollama nor Gemini answered."""


@dataclass(frozen=True)
class _Claim:
    db_path: Path | str
    claim_id: str
    agent: str
    masker: Masker


_claim: ContextVar[_Claim | None] = ContextVar("llm_claim", default=None)


@contextmanager
def for_claim(db_path: Path | str, claim_id: str, agent: str, masker: Masker) -> Iterator[None]:
    """LLM calls made inside this block mask with `masker` and are logged against the claim."""
    token = _claim.set(_Claim(db_path, claim_id, agent, masker))
    try:
        yield
    finally:
        _claim.reset(token)


def complete(prompt: str, *, system: str = "", seed: int | None = None, temperature: float = 0.3) -> str:
    claim = _claim.get()
    masker = claim.masker if claim else Masker()
    masked_prompt, masked_system = masker.mask(prompt), masker.mask(system)
    reply = None
    try:
        reply = _answer(masked_prompt, masked_system, seed, temperature)
    finally:
        if claim:
            audit.log(
                claim.db_path, claim.claim_id, claim.agent, "llm_call",
                {"system": masked_system, "prompt": masked_prompt, "reply": reply},
            )
    return masker.restore(reply)


def _answer(prompt: str, system: str, seed: int | None, temperature: float) -> str:
    errors = []
    try:
        return _ollama(prompt, system, seed, temperature)
    except (httpx.HTTPError, KeyError) as error:
        errors.append(f"Ollama: {error!r}")
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return _gemini(prompt, system, seed, temperature)
        except Exception as error:  # the SDK raises many types; any failure means no answer
            errors.append(f"Gemini: {error!r}")
    else:
        errors.append("Gemini: GEMINI_API_KEY is not set")
    raise LLMUnavailable("; ".join(errors))


def _ollama(prompt: str, system: str, seed: int | None, temperature: float) -> str:
    messages = [{"role": "user", "content": prompt}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    options: dict[str, float | int] = {"temperature": temperature}
    if seed is not None:
        options["seed"] = seed
    response = httpx.post(
        f"{os.environ.get('OLLAMA_URL', 'http://localhost:11434')}/api/chat",
        json={
            "model": os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
            "messages": messages,
            "stream": False,
            "options": options,
        },
        timeout=300,  # the first call loads the model, which can take a minute
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _gemini(prompt: str, system: str, seed: int | None, temperature: float) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system or None, temperature=temperature, seed=seed
        ),
    )
    return response.text or ""
