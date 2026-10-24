"""Appeal agent: answers a cut or a denial when the policy says the insurer should pay more (design section 4).

The rules engine prices the claim as the policy reads. Deductions the insurer made under a clause
beyond what the policy supports are contested; if there are none, or the gap is nothing, the
decision is accepted and nothing is sent. An appeal cites the contested clauses and the clinical
records in the claim, and only those records: it never cites a document the claim doesn't have.

A denial of the whole claim that the policy doesn't support (a fraud flag, a coding dispute) isn't
answered automatically: a person decides whether to appeal it.

The LLM writes only the clinical argument, from the records, and is given no amounts. The amounts
sentence is built in code from the engine's numbers. Every appeal goes to a person (gate).
"""

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from backend import llm
from backend.agents.base import HUMAN, AgentResult
from backend.agents.hospital.intake import Reading
from backend.rules.engine import assess
from backend.schemas import AgentOutput, Appeal, Case, Deduction, Deny, DocumentType, Finding, PartialApprove
from data.generator.render import TITLES

AGENT = "appeal"
# Records that show what was wrong and why it needed treating.
CLINICAL_RECORDS = [DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT, DocumentType.XRAY_REPORT, DocumentType.OPERATIVE_NOTE]

_SYSTEM = (
    "You write the clinical paragraph of an Indian hospital's appeal to a health insurer that cut a claim. "
    "Argue that the treatment and admission were medically necessary, using only the facts given, and cite "
    "each record by its id. Do not mention any amounts, prices or rupees. Three or four plain sentences."
)
_MONEY = re.compile(r"₹|\brs\.?\s*\d|\brupee|\binr\b", re.IGNORECASE)


class Contested(BaseModel):
    clause: str
    insurer_cut: int
    policy_cut: int  # what the policy, priced by the rules engine, cuts under this clause


class AppealDraft(BaseModel):
    appeal: Appeal | None  # None: accept the insurer's decision
    expected: int  # what the insurer should pay: the engine's payable, up to the amount requested
    approved: int
    contested: list[Contested]
    argument_by: Literal["llm", "template"] | None


def run(claim_id: str, decision: PartialApprove | Deny, case: Case, reading: Reading, requested: int, round: int) -> AgentResult:
    assessment = assess(case)
    expected = min(assessment.payable, requested)
    approved = decision.approved_amount if isinstance(decision, PartialApprove) else 0
    insurer_cuts = (
        decision.deductions if isinstance(decision, PartialApprove)
        else [Deduction(item=decision.reason, amount=assessment.billed, clause=decision.clause)]
    )
    policy_by_clause = _by_clause((d.clause_id, d.amount) for d in assessment.deductions)
    contested = [
        Contested(clause=clause, insurer_cut=amount, policy_cut=policy_by_clause.get(clause, 0))
        for clause, amount in _by_clause((d.clause, d.amount) for d in insurer_cuts).items()
        if amount > policy_by_clause.get(clause, 0)
    ]

    findings = [Finding(issue="Policy assessment", evidence=f"Insurer should pay ₹{expected:,}; it approved ₹{approved:,}", source="Rules engine")]
    if not contested or expected <= approved:
        findings.append(Finding(issue="Nothing to appeal", evidence="Every deduction is one the policy allows", source="Rules engine"))
        draft = AppealDraft(appeal=None, expected=expected, approved=approved, contested=[], argument_by=None)
        return _result(claim_id, "accept_decision", findings, "none", draft)
    if isinstance(decision, Deny):
        findings.append(Finding(issue="Whole claim denied", evidence=decision.reason, source=f"Insurer decision, round {decision.round}: {decision.clause}"))
        draft = AppealDraft(appeal=None, expected=expected, approved=approved, contested=contested, argument_by=None)
        return _result(claim_id, "needs_person", findings, HUMAN, draft)

    records = [doc for doc_type in CLINICAL_RECORDS if (doc := reading.get(doc_type)) is not None]
    reasons = [c.clause for c in contested]
    argument, written_by = _argument(reasons, records)
    amounts = " ".join(
        f"Under clause '{c.clause}' the insurer cut ₹{c.insurer_cut:,}, but the policy supports ₹{c.policy_cut:,}." for c in contested
    )
    appeal = Appeal(
        claim_id=claim_id,
        stage=decision.stage,
        round=round,
        argument=f"{argument}\n\n{amounts} As priced under the policy, ₹{expected:,} is payable; ₹{approved:,} was approved.",
        cited_clauses=sorted({*reasons, *policy_by_clause}),
        cited_documents=[doc.doc_id for doc in records],
    )
    known = {doc.doc_id for doc in reading.documents}
    if unknown := set(appeal.cited_documents) - known:  # never cite a record the claim doesn't have
        raise ValueError(f"appeal cites documents not in the claim: {sorted(unknown)}")

    findings += [Finding(issue=f"Contesting '{c.clause}'", evidence=f"Insurer cut ₹{c.insurer_cut:,}; policy supports ₹{c.policy_cut:,}", source=f"Insurer decision, round {decision.round}") for c in contested]
    findings += [Finding(issue=f"Citing {TITLES[doc.type]}", evidence="Clinical record in the claim", source=doc.source()) for doc in records]
    draft = AppealDraft(appeal=appeal, expected=expected, approved=approved, contested=contested, argument_by=written_by)
    return _result(claim_id, "appeal", findings, "human_approval", draft)  # an appeal always goes to a person


def _result(claim_id: str, decision: str, findings: list[Finding], next_step: str, draft: AppealDraft) -> AgentResult:
    return AgentResult(
        output=AgentOutput(agent=AGENT, claim_id=claim_id, decision=decision, confidence=0.9, findings=findings, next=next_step),
        data=draft,
    )


def _by_clause(cuts) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for clause, amount in cuts:
        totals[clause] += amount
    return dict(totals)


def _argument(reasons: list[str], records) -> tuple[str, Literal["llm", "template"]]:
    facts = [f"{TITLES[doc.type]} {doc.doc_id}: " + "; ".join(
        f"{name.replace('_', ' ')}: {field.value}" for name, field in doc.fields.items() if field.value not in (None, "")
    ) for doc in records]
    prompt = "Insurer's reason for the cut: " + "; ".join(reasons) + "\n" + "\n".join(facts)
    try:
        text = llm.complete(prompt, system=_SYSTEM, seed=7, temperature=0.2).strip()
    except llm.LLMUnavailable:
        text = ""
    if text and not _MONEY.search(text):
        return text, "llm"
    lines = [f"The insurer cut the claim citing: {'; '.join(reasons)}. The records in this claim show the treatment and admission were needed."]
    lines += [f"{fact}." for fact in facts]
    return " ".join(lines), "template"
