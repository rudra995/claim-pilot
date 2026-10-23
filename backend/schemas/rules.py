"""Policy rules as structured JSON (design section 6).

The LLM extracts these from policy wording; the rules engine applies them.
Each rule type has its own `value` shape, checked here so a bad extraction fails loudly.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.agent_output import NonEmptyStr
from backend.schemas.bill import BillCategory


class RuleType(StrEnum):
    ROOM_RENT_CAP = "room_rent_cap"
    ICU_CAP = "icu_cap"
    SUB_LIMIT = "sub_limit"
    WAITING_PERIOD = "waiting_period"
    CO_PAY = "co_pay"
    NON_PAYABLE = "non_payable"
    EXCLUSION = "exclusion"


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DailyCapValue(_Value):
    """room_rent_cap and icu_cap: a per-day cap, as a share of sum insured or a fixed amount."""

    percent_of_sum_insured: float | None = Field(default=None, gt=0, le=100)
    amount: int | None = Field(default=None, gt=0)
    per: Literal["day"] = "day"

    @model_validator(mode="after")
    def _exactly_one_cap(self) -> "DailyCapValue":
        if (self.percent_of_sum_insured is None) == (self.amount is None):
            raise ValueError("give exactly one of percent_of_sum_insured or amount")
        return self


class SubLimitValue(_Value):
    procedure: NonEmptyStr
    amount: int = Field(gt=0)


class WaitingPeriodValue(_Value):
    procedures: list[NonEmptyStr] = Field(min_length=1)
    months: int = Field(gt=0)


class CoPayValue(_Value):
    percent: float = Field(gt=0, le=100)
    min_age: int | None = Field(default=None, ge=0)  # applies only to patients this age or older


class NonPayableValue(_Value):
    categories: list[BillCategory] = Field(default_factory=list)
    items: list[NonEmptyStr] = Field(default_factory=list)  # matched against bill line descriptions

    @model_validator(mode="after")
    def _not_empty(self) -> "NonPayableValue":
        if not self.categories and not self.items:
            raise ValueError("non_payable needs at least one category or item")
        return self


class ExclusionValue(_Value):
    procedures: list[NonEmptyStr] = Field(min_length=1)


RuleValue = (
    DailyCapValue | SubLimitValue | WaitingPeriodValue | CoPayValue | NonPayableValue | ExclusionValue
)

VALUE_MODELS: dict[RuleType, type[_Value]] = {
    RuleType.ROOM_RENT_CAP: DailyCapValue,
    RuleType.ICU_CAP: DailyCapValue,
    RuleType.SUB_LIMIT: SubLimitValue,
    RuleType.WAITING_PERIOD: WaitingPeriodValue,
    RuleType.CO_PAY: CoPayValue,
    RuleType.NON_PAYABLE: NonPayableValue,
    RuleType.EXCLUSION: ExclusionValue,
}

_CAP_TYPES = {RuleType.ROOM_RENT_CAP, RuleType.ICU_CAP}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: NonEmptyStr  # e.g. "POL-A-4.2"
    type: RuleType
    value: RuleValue
    clause_id: NonEmptyStr  # e.g. "4.2"; every deduction cites this
    clause_text: NonEmptyStr
    proportionate: bool = False
    linked_charges: list[BillCategory] = Field(default_factory=list)
    excluded_from_proportionate: list[BillCategory] = Field(default_factory=list)
    verified_by_human: bool = False

    @model_validator(mode="before")
    @classmethod
    def _parse_value_for_type(cls, data: Any) -> Any:
        # Pick the value model from `type` instead of letting the union guess.
        if isinstance(data, dict) and isinstance(data.get("value"), dict):
            value_model = VALUE_MODELS.get(data.get("type"))  # type: ignore[arg-type]
            if value_model is not None:
                data = {**data, "value": value_model.model_validate(data["value"])}
        return data

    @model_validator(mode="after")
    def _check_proportionate(self) -> "Rule":
        if self.proportionate or self.linked_charges or self.excluded_from_proportionate:
            if self.type not in _CAP_TYPES:
                raise ValueError("proportionate deduction only applies to room_rent_cap or icu_cap")
        if self.proportionate and not self.linked_charges:
            raise ValueError("a proportionate rule must list its linked_charges")
        overlap = set(self.linked_charges) & set(self.excluded_from_proportionate)
        if overlap:
            raise ValueError(f"categories both linked and excluded: {sorted(overlap)}")
        return self
