"""Plain fixes for the flags behind a denial-risk score (design section 7).

The denial-risk model's SHAP values say which flags push a claim towards denial; this table says
what a billing officer can do about each one.
"""

FIXES: dict[str, str] = {
    "missing_admission_note": "Attach the admission note",
    "missing_lab_report": "Attach the lab reports",
    "missing_xray_report": "Attach the pre-op X-ray report",
    "missing_operative_note": "Attach the operative note",
    "missing_discharge_summary": "Attach the discharge summary",
    "admission_reason_missing": "Add to the discharge summary why admission was needed",
    "room_over_cap": "Room rent is above the policy cap: tell the patient about the proportionate"
    " deduction, or offer a room within the cap",
    "non_payables_billed": "Move non-payable items (gloves, masks, kits) to the patient's bill",
    "sub_limit_exceeded": "Request only up to the procedure's sub-limit; the rest is the patient's share",
    "waiting_period_breached": "The policy's waiting period isn't over: check the policy start date,"
    " or plan for the patient to pay",
    "excluded_procedure": "The policy excludes this procedure: plan for the patient to pay",
    "coding_mismatch": "Check that the ICD-10 code matches the diagnosis",
}
