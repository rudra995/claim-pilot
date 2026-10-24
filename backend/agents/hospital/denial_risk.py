"""Denial-risk agent: checks a package before it goes out, and says what to fix (design sections 4 and 7).

Two views, joined: a checklist (missing documents, rules-engine cuts, coding) that cites where each
problem is, and the denial model's score with SHAP weights saying which problems matter most. The
checklist uses the model's own flag definitions, so the two never disagree about what a flag means.
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.models.features import DOC_FLAGS, RULE_FLAGS, denial_features
from backend.models.fixes_map import FIXES
from backend.models.predict import denial_risk
from backend.rules.engine import assess
from backend.schemas import AgentOutput, Case, DocumentType, Finding
from data.generator.procedures import BY_SLUG

AGENT = "denial_risk"
RISK_THRESHOLD = 0.5  # above this chance of a cut or denial, a person looks before anything else
MODEL_SOURCE = "Denial-risk model (XGBoost + SHAP)"


class RiskItem(BaseModel):
    flag: str
    issue: str
    evidence: str
    source: str
    fix: str
    model_weight: float | None  # SHAP push towards a cut or denial, if among the model's top reasons


class RiskReport(BaseModel):
    risk: float  # chance the claim is not fully approved
    probabilities: dict[str, float]
    threshold: float
    items: list[RiskItem]  # strongest first

    @property
    def fixes(self) -> list[str]:
        return [item.fix for item in self.items]


def run(claim_id: str, case: Case) -> AgentResult:
    score = denial_risk(case)
    weights = {reason.flag: reason.impact for reason in score.reasons}
    raised = [flag for flag, value in denial_features(case).items() if flag in FIXES and value]
    items = [_item(flag, case, weights.get(flag)) for flag in raised]
    items.sort(key=lambda item: item.model_weight or 0, reverse=True)
    report = RiskReport(risk=score.risk, probabilities=score.probabilities, threshold=RISK_THRESHOLD, items=items)

    odds = ", ".join(f"{outcome.lower().replace('_', ' ')} {p:.0%}" for outcome, p in score.probabilities.items())
    findings = [Finding(issue="Denial risk", evidence=f"{score.risk:.0%} chance of a cut or denial ({odds})", source=MODEL_SOURCE)]
    findings += [Finding(issue=item.issue, evidence=item.evidence, source=item.source) for item in items]
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="fix_before_submit" if items else "ok_to_submit",
            confidence=max(score.probabilities.values()),
            findings=findings,
            next=HUMAN if score.risk >= RISK_THRESHOLD else "human_approval",
        ),
        data=report,
    )


def _item(flag: str, case: Case, weight: float | None) -> RiskItem:
    issue, evidence, source = _checklist(flag, case)
    if weight is not None:
        evidence += f"; model weight +{weight:.2f}"
    return RiskItem(flag=flag, issue=issue, evidence=evidence, source=source, fix=FIXES[flag], model_weight=weight)


def _checklist(flag: str, case: Case) -> tuple[str, str, str]:
    """(issue, evidence, source) for one raised flag, pointing at where the problem is."""
    procedure = case.admission.procedure.replace("_", " ")
    if flag in DOC_FLAGS:
        name = DOC_FLAGS[flag].value.replace("_", " ")
        attached = ", ".join(doc.doc_id for doc in case.documents) or "none"
        return f"Missing {name}", f"Not among the documents attached ({attached})", f"Document checklist for {procedure}"
    if flag == "admission_reason_missing":
        summary = next(d for d in case.documents if d.type == DocumentType.DISCHARGE_SUMMARY)
        return "Reason for admission not stated", "The discharge summary doesn't say why admission was needed", f"Discharge summary {summary.doc_id}"
    if flag in RULE_FLAGS:
        rule_types = {rule.rule_id: rule.type for rule in case.policy.rules}
        cuts = [d for d in assess(case).deductions if rule_types.get(d.rule_id) == RULE_FLAGS[flag]]
        clauses = ", ".join(sorted({d.clause_id for d in cuts}))
        reasons = "; ".join(sorted({d.reason for d in cuts}))
        total = sum(d.amount for d in cuts)
        return flag.replace("_", " ").capitalize(), f"{reasons}: ₹{total:,} not payable", f"Policy clause {clauses}"
    if flag == "coding_mismatch":
        expected = BY_SLUG[case.admission.procedure].icd10
        return "ICD-10 code doesn't match", f"Claim coded {case.admission.icd10}; {procedure} is {expected}", "ICD-10 lookup"
    raise ValueError(f"no checklist entry for {flag}")
