"""TPA medical reviewer: is the treatment justified by the records that were sent? (design section 5)

Checks the documents a claim like this needs, with the TPA's own habits on top: TPA-A won't pass an
orthopaedic claim without the pre-op X-ray report, and TPA-B won't pay for the stay if the
discharge summary doesn't say why admission was needed, unless the hospital has since pointed to
clinical records that show it.
"""

from pydantic import BaseModel

from backend.agents.base import AgentResult
from backend.schemas import AgentOutput, Case, Document, DocumentType, Finding
from data.generator.admissions import ADMISSION_REASON, required_documents
from data.generator.procedures import BY_SLUG
from data.generator.render import TITLES
from data.generator.tpa_profiles import TPA_PROFILES

AGENT = "tpa_medical_reviewer"
_DEFAULT_REQUIRED = [DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT, DocumentType.DISCHARGE_SUMMARY]


class Gap(BaseModel):
    missing: str  # what to ask for
    reason: str  # why it is needed


class MedicalReview(BaseModel):
    gaps: list[Gap]  # documents to ask for
    stay_unjustified: bool  # the TPA cuts stay charges for this


def run(claim_id: str, case: Case, attached: list[Document], necessity_argued: bool) -> AgentResult:
    profile = TPA_PROFILES[case.admission.tpa_id]
    procedure = BY_SLUG.get(case.admission.procedure)
    name = case.admission.procedure.replace("_", " ")
    present = {doc.type: doc for doc in attached}
    attached_ids = ", ".join(doc.doc_id for doc in attached) or "none"

    gaps = []
    for doc_type in required_documents(procedure) if procedure else _DEFAULT_REQUIRED:
        if doc_type in present:
            continue
        if doc_type == DocumentType.XRAY_REPORT:
            if profile.requires_xray_for_ortho:
                gaps.append(Gap(missing=f"Pre-op {TITLES[doc_type]}", reason=f"Orthopaedic claims need the pre-op X-ray report to confirm that {name} was needed"))
        else:
            gaps.append(Gap(missing=TITLES[doc_type], reason=f"A {name} claim needs the {TITLES[doc_type].lower()}"))

    summary = present.get(DocumentType.DISCHARGE_SUMMARY)
    reason_missing = summary is not None and not summary.fields.get(ADMISSION_REASON)
    stay_unjustified = reason_missing and profile.denies_without_admission_reason and not necessity_argued

    findings = [Finding(issue=f"Missing {gap.missing}", evidence=gap.reason, source=f"Documents attached: {attached_ids}") for gap in gaps]
    if stay_unjustified:
        findings.append(Finding(issue="Admission not justified", evidence="The discharge summary doesn't say why admission was needed", source=f"Discharge summary {summary.doc_id}"))
    elif reason_missing and necessity_argued:
        findings.append(Finding(issue="Admission justified on appeal", evidence="The hospital pointed to clinical records showing admission was needed", source="Hospital's appeal or response"))
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
        data=MedicalReview(gaps=gaps, stay_unjustified=stay_unjustified),
    )
