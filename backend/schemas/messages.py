"""Messages between hospital and insurer on the claim exchange (design section 5).

The two sides share nothing but these messages. Amounts are whole rupees.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from backend.schemas.agent_output import NonEmptyStr

MAX_ROUNDS = 3


class MessageType(StrEnum):
    SUBMIT = "SUBMIT"
    QUERY = "QUERY"
    RESPONSE = "RESPONSE"
    APPROVE = "APPROVE"
    PARTIAL_APPROVE = "PARTIAL_APPROVE"
    DENY = "DENY"
    APPEAL = "APPEAL"
    FINAL = "FINAL"


class Sender(StrEnum):
    HOSPITAL = "hospital"
    INSURER = "insurer"


class Stage(StrEnum):
    PREAUTH = "preauth"
    ENHANCEMENT = "enhancement"
    DISCHARGE = "discharge"


class Deduction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: NonEmptyStr
    amount: int = Field(ge=0)
    clause: NonEmptyStr  # every deduction cites a clause


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: NonEmptyStr
    stage: Stage
    round: int = Field(ge=1, le=MAX_ROUNDS)


class _HospitalMessage(_Message):
    @property
    def sender(self) -> Sender:
        return Sender.HOSPITAL


class _InsurerMessage(_Message):
    @property
    def sender(self) -> Sender:
        return Sender.INSURER


class Submit(_HospitalMessage):
    type: Literal[MessageType.SUBMIT] = MessageType.SUBMIT
    package: dict[str, Any]
    documents: list[NonEmptyStr]  # doc_ids attached
    requested_amount: int = Field(ge=0)


class Query(_InsurerMessage):
    type: Literal[MessageType.QUERY] = MessageType.QUERY
    missing: NonEmptyStr
    reason: NonEmptyStr


class Response(_HospitalMessage):
    type: Literal[MessageType.RESPONSE] = MessageType.RESPONSE
    answer: NonEmptyStr
    documents: list[NonEmptyStr] = Field(default_factory=list)


class Approve(_InsurerMessage):
    type: Literal[MessageType.APPROVE] = MessageType.APPROVE
    approved_amount: int = Field(ge=0)
    deductions: list[Deduction] = Field(default_factory=list)


class PartialApprove(_InsurerMessage):
    type: Literal[MessageType.PARTIAL_APPROVE] = MessageType.PARTIAL_APPROVE
    approved_amount: int = Field(ge=0)
    deductions: list[Deduction] = Field(min_length=1)


class Deny(_InsurerMessage):
    type: Literal[MessageType.DENY] = MessageType.DENY
    reason: NonEmptyStr
    clause: NonEmptyStr


class Appeal(_HospitalMessage):
    type: Literal[MessageType.APPEAL] = MessageType.APPEAL
    argument: NonEmptyStr
    cited_clauses: list[NonEmptyStr] = Field(min_length=1)
    cited_documents: list[NonEmptyStr] = Field(default_factory=list)  # must exist in the claim


class Final(_InsurerMessage):
    type: Literal[MessageType.FINAL] = MessageType.FINAL
    decision: Literal[MessageType.APPROVE, MessageType.PARTIAL_APPROVE, MessageType.DENY]
    approved_amount: int = Field(ge=0)
    deductions: list[Deduction] = Field(default_factory=list)


ClaimMessage = Annotated[
    Submit | Query | Response | Approve | PartialApprove | Deny | Appeal | Final,
    Field(discriminator="type"),
]

claim_message_adapter: TypeAdapter[ClaimMessage] = TypeAdapter(ClaimMessage)
