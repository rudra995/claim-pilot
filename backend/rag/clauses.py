"""Split a policy PDF into clauses by clause number, not fixed-size chunks (design section 6).

Policies number clauses differently ("4.2", "3.1 (a)", "C. BENEFITS", "2)"), and lines inside a
clause often start with list numbers ("1. Cost of pharmacy"). So a numbered line only starts a new
clause if its number follows on from the previous clause (the next sibling at some level, or the
first child) and no number that belongs before it turns up later in the policy. List items restart
at 1, or run on out of order ("7." in a list inside 6.2, before 6.3), so they stay in their clause.
Annexures (lists of non-payable items, day-care procedures) are one clause each.
Clauses in the definitions section are marked as definitions, so search can keep them apart.
"""

import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import fitz

# A clause number at the start of a line: "4", "4.2", "4.2.1", or a section letter "C".
_HEADING = re.compile(r"^\s*([A-Z]|\d{1,2}(?:\.\d{1,2}){0,3})\s*[.)]?\s+(?P<title>[A-Z(].*)$")
_NUMBER_ONLY = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[.)]?\s*$")
_ALL_CAPS_TITLE = re.compile(r"^[A-Z][A-Z &/,'()-]{3,}")
_ANNEXURE = re.compile(r"^\s*annexure\s*[-–:]?\s*([A-Z]{1,4}|\d{1,2})\b\s*[-–:]?\s*(?P<title>[^a-z\s].*)?$", re.I)
MAX_JUMP = 2  # tolerate one clause number lost in PDF extraction
LOOKAHEAD_LINES = 150  # how far ahead an out-of-order number marks a list item


@dataclass(frozen=True)
class Clause:
    policy_id: str
    clause_id: str  # e.g. "4.1", or "C.1" in policies with lettered sections
    title: str
    page: int  # first page, from 1
    text: str
    kind: str = "clause"  # "clause", "definition" (the definitions section) or "annexure"


def read_pdf_lines(path: Path) -> list[tuple[int, str]]:
    """(page, line) for every line, without the headers and footers repeated on most pages."""
    pages = [_page_text(page) for page in fitz.open(path)]
    lines = [(i, line.strip()) for i, text in enumerate(pages, start=1) for line in text.splitlines()]
    return [(page, line) for page, line in drop_running_heads(lines, len(pages)) if line]


def _page_text(page: fitz.Page) -> str:
    """Page text in reading order; two-column pages are read left column first, then right."""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]  # text blocks only
    middle = page.rect.width / 2
    narrow = [b for b in blocks if b[2] - b[0] < page.rect.width * 0.55]
    right = [b for b in narrow if b[0] >= middle]
    if len(blocks) >= 6 and len(narrow) >= 0.8 * len(blocks) and len(right) >= 0.25 * len(blocks):
        blocks = sorted(blocks, key=lambda b: (b[0] >= middle, b[1]))
    return "\n".join(b[4] for b in blocks)


def drop_running_heads(lines: list[tuple[int, str]], n_pages: int) -> list[tuple[int, str]]:
    def shape(line: str) -> str:
        return re.sub(r"\d+", "#", line)

    # Only lines with words count: a bare "1." on most pages is list numbering, not a header.
    on_pages = Counter(s for _, s in {(page, shape(line)) for page, line in lines if re.search(r"[A-Za-z]{3}", line)})
    repeated = {s for s, count in on_pages.items() if n_pages >= 4 and count >= n_pages / 2}
    return [(page, line) for page, line in lines if shape(line) not in repeated]


def split_clauses(policy_id: str, lines: list[tuple[int, str]]) -> list[Clause]:
    lines = _join_bare_numbers(lines)
    lettered = any(_section_letter(line) for _, line in lines[:40])
    headings = _headings(lines, lettered)

    clauses: list[Clause] = []
    clause_id, title, start_page, body = "0", "Front matter", 1, []
    for i, (page, line) in enumerate(lines):
        if i in headings:
            if body:
                clauses.append(Clause(policy_id, clause_id, title, start_page, " ".join(body)))
            (clause_id, title), start_page, body = headings[i], page, []
        body.append(line)
    if body:
        clauses.append(Clause(policy_id, clause_id, title, start_page, " ".join(body)))
    return _with_kinds(_merge_duplicate_ids(clauses))


def _with_kinds(clauses: list[Clause]) -> list[Clause]:
    sections = {c.clause_id: c.title.upper() for c in clauses if "." not in c.clause_id}
    out = []
    for c in clauses:
        if c.clause_id.startswith("Annexure"):
            kind = "annexure"
        elif "DEFINITION" in sections.get(c.clause_id.split(".")[0], "") and "." in c.clause_id:
            kind = "definition"
        else:
            kind = "clause"
        out.append(replace(c, kind=kind))
    return out


def _headings(lines: list[tuple[int, str]], lettered: bool) -> dict[int, tuple[str, str]]:
    """Line index -> (clause id, title) for every line that starts a clause."""
    numbered: list[tuple[int, tuple[int, ...]]] = []  # every numbered line, in order
    annexures: dict[int, tuple[str, str]] = {}
    section = 0
    for i, (_, line) in enumerate(lines):
        if annexure := _ANNEXURE.match(line):
            label = annexure.group(1).upper()
            annexures[i] = (f"Annexure {label}", (annexure.group("title") or f"Annexure {label}").strip()[:80])
        elif (number := _parse(line, section, lettered)) is not None:
            if len(number) == 1 and lettered:
                section = number[0]
            numbered.append((i, number))

    headings: dict[int, tuple[str, str]] = dict(annexures)
    first_annexure = min(annexures, default=len(lines))
    current: tuple[int, ...] | None = None
    for k, (i, number) in enumerate(numbered):
        if i > first_annexure:
            break  # annexures are lists; nothing numbered inside them starts a clause
        if current is not None and not _follows(number, current):
            continue
        if current is not None and any(
            current < later < number for j, later in numbered[k + 1:] if j - i <= LOOKAHEAD_LINES
        ):
            continue  # something that belongs before this comes just after it: this is a list item
        current = number
        headings[i] = (_format(number, lettered), _HEADING.match(lines[i][1]).group("title")[:80].strip())  # type: ignore[union-attr]
    return headings


def load_clauses(path: Path) -> list[Clause]:
    return split_clauses(path.stem, read_pdf_lines(path))


def _join_bare_numbers(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """'1.' on its own line followed by 'Hospitalization' becomes '1. Hospitalization'."""
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        page, line = lines[i]
        if _NUMBER_ONLY.match(line) and i + 1 < len(lines):
            out.append((page, f"{line} {lines[i + 1][1]}"))
            i += 2
        else:
            out.append((page, line))
            i += 1
    return out


def _section_letter(line: str) -> bool:
    match = _HEADING.match(line)
    return bool(match and match.group(1).isalpha() and _ALL_CAPS_TITLE.match(match.group("title")))


def _parse(line: str, section: int, lettered: bool) -> tuple[int, ...] | None:
    match = _HEADING.match(line)
    if not match:
        return None
    label = match.group(1)
    if label.isalpha():
        # Section letters are headings only in lettered policies, and only with an all-caps title.
        if not (lettered and _ALL_CAPS_TITLE.match(match.group("title"))):
            return None
        return (ord(label) - ord("A") + 1,)
    number = tuple(int(part) for part in label.split("."))
    if lettered and section:
        number = (section, *number)  # "1.1" inside section C is C.1.1
    return number


def _follows(number: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """True if `number` comes right after `current`: the next sibling at some level, or a first child."""
    extra = number[len(current):]
    if number[: len(current)] == current and extra and 1 <= extra[0] <= MAX_JUMP and all(p == 1 for p in extra[1:]):
        return True
    for level in range(len(current)):
        rest = number[level + 1:]
        if (
            len(number) > level
            and number[:level] == current[:level]
            and 1 <= number[level] - current[level] <= MAX_JUMP
            and all(part == 1 for part in rest)
        ):
            return True
    return False


def _format(number: tuple[int, ...], lettered: bool) -> str:
    if lettered:
        return ".".join([chr(ord("A") + number[0] - 1), *map(str, number[1:])])
    return ".".join(map(str, number))


def _merge_duplicate_ids(clauses: list[Clause]) -> list[Clause]:
    """Two-column layouts can revisit a clause number; keep one clause per id."""
    merged: dict[str, Clause] = {}
    for clause in clauses:
        if clause.clause_id in merged:
            first = merged[clause.clause_id]
            merged[clause.clause_id] = replace(first, text=f"{first.text} {clause.text}")
        else:
            merged[clause.clause_id] = clause
    return list(merged.values())
