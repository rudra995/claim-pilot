from enum import StrEnum


class BillCategory(StrEnum):
    """Bill line categories. Rules refer to these (linked charges, non-payables)."""

    ROOM = "room"
    ICU = "icu"
    DOCTOR_FEES = "doctor_fees"
    NURSING = "nursing"
    OT_CHARGES = "ot_charges"
    IMPLANTS = "implants"
    PHARMACY = "pharmacy"
    INVESTIGATIONS = "investigations"
    CONSUMABLES = "consumables"
    OTHER = "other"
