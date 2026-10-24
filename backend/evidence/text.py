"""Read a document's text page by page. Document text is untrusted data: it is read, never obeyed.

A page with no text layer (a scan) comes back empty; there is no OCR here, so callers must treat an
empty page as unread, not as "says nothing".
"""

import re
from functools import lru_cache
from pathlib import Path

import fitz

# Phrases that try to give the system an order rather than describe a patient. Finding one never
# changes what the system does; it is flagged so a person looks at the document.
_INSTRUCTION = re.compile(
    r"\b(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the )?(policy|rules|instructions)"
    r"|(skip|bypass|override) (the )?(human |manual )?(approval|review|checks?)|auto-?approve"
    r"|approve this claim (immediately|without)|system prompt|you are (now )?an? (ai|assistant|model))\b",
    re.IGNORECASE,
)


def read_pages(path: str | Path | None) -> list[str]:
    """Text of each page (1-based order); [] if there is no file."""
    if path is None or not Path(path).exists():
        return []
    return list(_read(str(path), Path(path).stat().st_mtime_ns))


@lru_cache(maxsize=256)
def _read(path: str, _mtime: int) -> tuple[str, ...]:
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    with fitz.open(path) as pdf:
        return tuple(page.get_text(flags=flags) for page in pdf)


def readable(pages: list[str]) -> bool:
    return any(page.strip() for page in pages)


def instructions_in(pages: list[str]) -> list[tuple[int, str]]:
    """(page, phrase) for each instruction-like phrase in the text."""
    return [(number, match.group(0)) for number, page in enumerate(pages, start=1) for match in _INSTRUCTION.finditer(page)]
