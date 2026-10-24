"""TPA medical reviewer: is the treatment justified by the records that were sent? (design section 5)

Checks the documents a claim like this needs, with the TPA's own habits on top: TPA-A won't pass an
orthopaedic claim without the pre-op X-ray report, and TPA-B won't pay for the stay if the
discharge summary doesn't say why admission was needed.

The reviewer reads the documents' text, as a real reviewer would, not the generator's ground truth.
A hospital can answer the TPA-B cut by citing clinical records, but citing a record is not enough:
the cited records must actually show why admission was needed (backend/evidence/clinical.py). Only
"supported" evidence lifts the cut; anything else keeps it, and says why.
"""

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.evidence.clinical import Assessment, Verdict, states_admission_reason
from backend.schemas import AgentOutput, Case, Document, DocumentType, Finding, Stage
from data.generator.admissions import required_documents
from data.generator.procedures import BY_SLUG
from data.generator.render import TITLES
from data.generator.tpa_profiles import TPA_PROFILES

AGENT = "tpa_medical_reviewer"
_DEFAULT_REQUIRED = [DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT, DocumentType.DISCHARGE_SUMMARY]
# Documents that exist only once the patient has been treated or discharged; a pre-auth can't have them.
LATER_DOCUMENTS = {DocumentType.OPERATIVE_NOTE, DocumentType.DISCHARGE_SUMMARY, DocumentType.FINAL_BILL}


class Gap(BaseModel):
    missing: str  # what to ask for
    reason: str  # why it is needed


class MedicalReview(BaseModel):
    gaps: list[Gap]  # documents to ask for
    stay_unjustified: bool  # the TPA cuts stay charges for this
    necessity: Assessment | None = None  # the check on the hospital's cited records, if it cited any


def required_for(case: Case, stage: Stage) -> list[DocumentType]:
    procedure = BY_SLUG.get(case.admission.procedure)
    required = required_documents(procedure) if procedure else _DEFAULT_REQUIRED
    if stage == Stage.DISCHARGE:
        return list(required)
    return [doc_type for doc_type in required if doc_type not in LATER_DOCUMENTS]


def run(
    claim_id: str, case: Case, attached: list[Document], necessity: Assessment | None, stage: Stage = Stage.DISCHARGE
) -> AgentResult:
    profile = TPA_PROFILES[case.admission.tpa_id]
    name = case.admission.procedure.replace("_", " ")
    present = {doc.type: doc for doc in attached}
    attached_ids = ", ".join(doc.doc_id for doc in attached) or "none"

    gaps = []
    for doc_type in required_for(case, stage):
        if doc_type in present:
            continue
        if doc_type == DocumentType.XRAY_REPORT:
            if profile.requires_xray_for_ortho:
                gaps.append(Gap(missing=f"Pre-op {TITLES[doc_type]}", reason=f"Orthopaedic claims need the pre-op X-ray report to confirm that {name} was needed"))
        else:
            gaps.append(Gap(missing=TITLES[doc_type], reason=f"A {name} claim needs the {TITLES[doc_type].lower()}"))

    summary = present.get(DocumentType.DISCHARGE_SUMMARY)
    stated, stated_passage = states_admission_reason(summary) if summary else (None, None)
    reason_missing = summary is not None and not stated
    argued = necessity is not None and necessity.supported
    stay_unjustified = reason_missing and profile.denies_without_admission_reason and not argued

    findings = [Finding(issue=f"Missing {gap.missing}", evidence=gap.reason, source=f"Documents attached: {attached_ids}") for gap in gaps]
    if reason_missing and profile.denies_without_admission_reason:
        why = "has no readable text" if stated is None else "doesn't say why admission was needed"
        findings.append(Finding(issue="Reason for admission not stated", evidence=f"The discharge summary {why}", source=f"Discharge summary {summary.doc_id}"))
    if necessity is not None and reason_missing:
        findings += _necessity_findings(necessity)
    if stated_passage is not None:
        findings.append(Finding(issue="Reason for admission stated", evidence=stated_passage.text, source=f"{summary.doc_id} p.{stated_passage.page}"))
    if not findings:
        findings.append(Finding(issue="Treatment justified", evidence="Every document this claim needs is attached", source=f"Documents attached: {attached_ids}"))

    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="gaps_found" if gaps or stay_unjustified else "justified",
            confidence=1.0,
            findings=findings,
            next="tpa_adjudicator",
        ),
        data=MedicalReview(gaps=gaps, stay_unjustified=stay_unjustified, necessity=necessity),
    )


def _necessity_findings(necessity: Assessment) -> list[Finding]:
    """What the check of the hospital's cited records found, each point tied to a page."""
    issue = {
        Verdict.SUPPORTED: "Admission justified by the cited records",
        Verdict.INSUFFICIENT: "Cited records don't show enough",
        Verdict.UNSUPPORTED: "Cited records don't show why admission was needed",
        Verdict.CONTRADICTORY: "Cited records contradict the need for admission",
    }[necessity.verdict]
    findings = [Finding(issue=issue, evidence=necessity.reason, source="Hospital's cited records: " + (", ".join(necessity.checked) or "none readable"))]
    findings += [Finding(issue=p.finding, evidence=p.text, source=f"{p.doc_id} p.{p.page}") for p in necessity.passages[:6]]
    findings += [Finding(issue="Citation rejected", evidence=problem.describe(), source="Claim file sent with the claim") for problem in necessity.problems]
    return findings
