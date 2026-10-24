"""Clinical coder: turns the diagnosis written in the documents into an ICD-10 code.

The lookup is the hospital's code list (the diagnoses it treats). The diagnosis text is matched by
shared words, conservatively: negated parts ("appendicitis ruled out", "no fracture") are dropped
first, an uncertain diagnosis ("suspected", "query", "?") is not coded, and a match must share at
least MIN_MATCH of its words. A weak match, a tie between two codes, or no match goes to a person,
who keeps the code entered at admission until they decide. The coder abstains rather than guesses.
"""

import re

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.agents.hospital.intake import Reading
from backend.schemas import Admission, AgentOutput, DocumentType, Finding
from data.generator.procedures import PROCEDURES

AGENT = "clinical_coder"

# ICD-10 code -> diagnosis, from the hospital's code list.
ICD10_LOOKUP: dict[str, str] = {p.icd10: p.diagnosis for p in PROCEDURES}

MIN_MATCH = 0.6  # share of words in common (Jaccard) below which the coder abstains
_NEGATED = re.compile(r"\b(ruled out|rule out|r/o|no evidence of|negative for|not|no|without|excluded)\b", re.IGNORECASE)
_UNCERTAIN = re.compile(r"(\?|\b(suspected|query|possible|probable|likely|differential|vs\.?|versus)\b)", re.IGNORECASE)
_IGNORED_WORDS = {"of", "the", "and", "with", "by", "on", "in", "right", "left", "bilateral"}
# Where the diagnosis is written, most trusted first at pre-auth time.
_DIAGNOSIS_FIELDS = [
    (DocumentType.ADMISSION_NOTE, "provisional_diagnosis"),
    (DocumentType.DISCHARGE_SUMMARY, "diagnosis"),
]


class Coding(BaseModel):
    icd10: str
    diagnosis: str  # the lookup's name for the code
    written_as: str | None  # the text in the document
    source: str
    reasoning: str


def run(claim_id: str, reading: Reading, admission: Admission) -> AgentResult:
    written, source = _written_diagnosis(reading)
    findings: list[Finding] = []
    asserted = _asserted(written or "")
    uncertain = bool(written) and bool(_UNCERTAIN.search(asserted))

    ranked = sorted(((_overlap(asserted, name), code) for code, name in ICD10_LOOKUP.items()), reverse=True)
    best_score, best_code = ranked[0]
    tie = len(ranked) > 1 and ranked[1][0] == best_score and best_score > 0

    if written is None or uncertain or best_score < MIN_MATCH:
        code, score = admission.icd10, 0.0
        why = "the diagnosis is written as uncertain" if uncertain else "no diagnosis in the lookup matches closely enough"
        reasoning = f"Not coded: {why}; keeping the code entered at admission ({code}) until a person decides"
        findings.append(
            Finding(issue="Diagnosis could not be coded", evidence=f"{written or 'No diagnosis written in the documents'} ({why})", source=source)
        )
    else:
        code, score = best_code, best_score
        reasoning = f"'{written}' matches '{ICD10_LOOKUP[code]}' in the ICD-10 lookup"
        findings.append(Finding(issue=f"Coded as {code}", evidence=reasoning, source=source))
        if tie:
            other = ranked[1][1]
            findings.append(
                Finding(
                    issue="Two codes fit equally",
                    evidence=f"{code} ({ICD10_LOOKUP[code]}) and {other} ({ICD10_LOOKUP[other]})",
                    source=source,
                )
            )

    if code != admission.icd10:
        findings.append(
            Finding(
                issue="Code differs from the one entered at admission",
                evidence=f"Documents support {code}; admission record has {admission.icd10}",
                source=f"Admission record {admission.admission_id}",
            )
        )
    summary_code = reading.value(DocumentType.DISCHARGE_SUMMARY, "icd10")
    if summary_code and summary_code != code:
        summary = reading.get(DocumentType.DISCHARGE_SUMMARY)
        findings.append(
            Finding(
                issue="Code differs from the discharge summary",
                evidence=f"Documents support {code}; discharge summary says {summary_code}",
                source=summary.source(summary.fields["icd10"].page),
            )
        )

    needs_person = score == 0 or tie
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="needs_review" if needs_person else "coded",
            confidence=round(score / 2 if tie else score, 3),
            findings=findings,
            next=HUMAN if needs_person else "policy_and_cost",
        ),
        data=Coding(
            icd10=code, diagnosis=ICD10_LOOKUP.get(code, code), written_as=written, source=source, reasoning=reasoning
        ),
    )


def _written_diagnosis(reading: Reading) -> tuple[str | None, str]:
    for doc_type, name in _DIAGNOSIS_FIELDS:
        doc = reading.get(doc_type)
        if doc and name in doc.fields and doc.fields[name].value:
            return str(doc.fields[name].value), doc.source(doc.fields[name].page)
    return None, "Claim documents"


def _asserted(text: str) -> str:
    """The diagnosis with its negated parts removed: "Gastroenteritis; appendicitis ruled out" -> "Gastroenteritis"."""
    parts = re.split(r"[;,.]|\bbut\b", text)
    return " ".join(part for part in parts if not _NEGATED.search(part)).strip()


def _overlap(text: str, name: str) -> float:
    """Share of words the two have in common (Jaccard), ignoring filler and laterality."""
    a, b = _words(text), _words(name)
    return len(a & b) / len(a | b) if a | b else 0.0


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower())) - _IGNORED_WORDS
