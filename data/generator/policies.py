"""Three synthetic insurance products and the rules each policy carries (design section 6)."""

from dataclasses import dataclass
from datetime import date

from backend.schemas import BillCategory, Policy, Rule

SUM_INSURED_OPTIONS = [300_000, 500_000, 750_000, 1_000_000, 1_500_000]
ROOM_CAP_PERCENT = 1.0  # every product caps room rent at 1% of sum insured per day

SUB_LIMITS = {
    "cataract_surgery": 40_000,
    "tonsillectomy": 30_000,
    "lithotripsy": 50_000,
    "hernia_repair": 60_000,
}
WAITING_PERIOD_MONTHS = 24
WAITING_PERIOD_PROCEDURES = [
    "total_knee_replacement",
    "total_hip_replacement",
    "cataract_surgery",
    "hernia_repair",
    "hysterectomy",
    "turp",
]
NON_PAYABLE_ITEMS = ["gloves", "mask", "admission kit"]


@dataclass(frozen=True)
class Product:
    code: str
    insurer: str
    name: str
    room_clause: str
    room_linked: tuple[BillCategory, ...]
    non_payable_clause: str
    sub_limit_clause: str
    waiting_clause: str
    exclusion_clause: str
    co_pay_clause: str | None = None  # 10% co-pay for patients aged 60 or above


_STANDARD_LINKED = (BillCategory.DOCTOR_FEES, BillCategory.NURSING, BillCategory.OT_CHARGES)

PRODUCTS = [
    Product("A", "Demo Insurer A", "Health Secure Individual", "4.2", _STANDARD_LINKED,
            "Annexure II", "4.5", "5.3", "6.2"),
    Product("B", "Demo Insurer B", "Senior Care Plus", "2.4", _STANDARD_LINKED,
            "Annexure III", "3.6", "4.1", "5.4", co_pay_clause="6.1"),
    Product("C", "Demo Insurer C", "Family Health Shield", "3.1", (*_STANDARD_LINKED, BillCategory.PHARMACY),
            "Annexure I", "3.8", "4.4", "5.1"),
]


def make_policy(policy_id: str, product: Product, sum_insured: int, start_date: date) -> Policy:
    rules: list[dict] = [
        {
            "type": "room_rent_cap",
            "value": {"percent_of_sum_insured": ROOM_CAP_PERCENT, "per": "day"},
            "clause_id": product.room_clause,
            "clause_text": "Room rent is payable up to 1% of the sum insured per day. If a higher room is"
            " chosen, associated medical expenses are paid in the same proportion.",
            "proportionate": True,
            "linked_charges": list(product.room_linked),
            "excluded_from_proportionate": [
                c for c in (BillCategory.IMPLANTS, BillCategory.PHARMACY) if c not in product.room_linked
            ],
        },
        {
            "type": "non_payable",
            "value": {"categories": [BillCategory.CONSUMABLES], "items": NON_PAYABLE_ITEMS},
            "clause_id": product.non_payable_clause,
            "clause_text": f"Items listed in {product.non_payable_clause}, including gloves, masks and"
            " admission kits, are not payable.",
        },
        *(
            {
                "type": "sub_limit",
                "value": {"procedure": procedure, "amount": amount},
                "clause_id": product.sub_limit_clause,
                "clause_text": f"Claims for {procedure.replace('_', ' ')} are payable up to ₹{amount:,}.",
            }
            for procedure, amount in SUB_LIMITS.items()
        ),
        {
            "type": "waiting_period",
            "value": {"procedures": WAITING_PERIOD_PROCEDURES, "months": WAITING_PERIOD_MONTHS},
            "clause_id": product.waiting_clause,
            "clause_text": "Joint replacement, cataract, hernia, hysterectomy and prostate surgery are"
            " covered only after 24 months of continuous coverage.",
        },
        {
            "type": "exclusion",
            "value": {"procedures": ["cosmetic_surgery", "bariatric_surgery"]},
            "clause_id": product.exclusion_clause,
            "clause_text": "Cosmetic and weight-loss surgery are not covered.",
        },
    ]
    if product.co_pay_clause:
        rules.append(
            {
                "type": "co_pay",
                "value": {"percent": 10.0, "min_age": 60},
                "clause_id": product.co_pay_clause,
                "clause_text": "A co-payment of 10% of every admissible claim applies to insured persons"
                " aged 60 or above.",
            }
        )
    return Policy(
        policy_id=policy_id,
        insurer=product.insurer,
        product=product.name,
        sum_insured=sum_insured,
        start_date=start_date,
        rules=[
            Rule.model_validate({"rule_id": f"{policy_id}-R{i}", "verified_by_human": True, **rule})
            for i, rule in enumerate(rules, start=1)
        ],
    )
