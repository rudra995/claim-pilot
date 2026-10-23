from enum import StrEnum


class ClaimState(StrEnum):
    """States a claim moves through. Only the orchestrator changes them (design section 3)."""

    ADMITTED = "ADMITTED"
    PREAUTH_DRAFT = "PREAUTH_DRAFT"
    RISK_CHECK = "RISK_CHECK"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    SUBMITTED = "SUBMITTED"
    QUERY_RAISED = "QUERY_RAISED"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    DENIED = "DENIED"
    IN_TREATMENT = "IN_TREATMENT"
    DISCHARGE_DRAFT = "DISCHARGE_DRAFT"
    FINAL_APPROVED = "FINAL_APPROVED"
    SETTLED = "SETTLED"
    CLOSED = "CLOSED"
