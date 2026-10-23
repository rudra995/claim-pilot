"""One small LLM client for the project: local Ollama first, Gemini as the fallback.

    OLLAMA_MODEL    local model (default qwen2.5-coder:7b)
    OLLAMA_URL      default http://localhost:11434
    GEMINI_API_KEY  enables the fallback
    GEMINI_MODEL    default gemini-2.5-flash

Callers never send patient names or ID numbers (design section 9).
"""

import os

import httpx


class LLMUnavailable(RuntimeError):
    """Neither Ollama nor Gemini answered."""


def complete(prompt: str, *, system: str = "", seed: int | None = None, temperature: float = 0.3) -> str:
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
