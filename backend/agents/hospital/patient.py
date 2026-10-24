"""Patient agent: a plain-language cost note with room advice, for staff to check and hand over (design sections 4, 10).

The room advisor prices the same stay in each room on the hospital's tariff with the rules engine's
`what_if_room`. Only when the room is over the policy's cap does it recommend a switch: to the dearest
room that costs the patient no more than the cheapest, the best room the policy fully supports. A
room within the cap is never second-guessed, even when a percentage co-pay makes a cheaper one
slightly cheaper. Every amount comes from the engine; the note is built in
code. It never mentions fraud. A note that tells the patient the claim isn't covered goes to a person.

The options are tariff scenarios, not live bed availability, and each prices the whole stay as if
it had been in that room. The note says so, says whether it is an admission estimate or worked out
on the final bill, cites the policy's room clause, and doesn't suggest that days already spent can
be repriced: a move changes the charges from the day of the move only.
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from typing import Literal

from backend.rules.engine import RoomScenario, assess, room_scenarios
from backend.schemas import AgentOutput, BillCategory, Case, Finding, RuleType
from data.generator.admissions import ROOMS

AGENT = "patient"
TARIFF = ROOMS  # the hospital's rooms: slug, label, rate per day


RoomOption = RoomScenario  # the engine prices each option, saving included


class PatientNote(BaseModel):
    options: list[RoomOption]  # dearest first
    recommended: RoomOption | None  # None: the current room is already the best value
    note: str
    basis: Literal["admission_estimate", "final_bill"] = "admission_estimate"
    clause: str | None = None  # the policy's room clause, quoted
    assumptions: list[str] = []


def run(claim_id: str, case: Case, basis: Literal["admission_estimate", "final_bill"] = "admission_estimate") -> AgentResult:
    room_lines = [line for line in case.bill_lines if line.category == BillCategory.ROOM]
    current = assess(case)
    labels = {slug: label for slug, label, _ in TARIFF}
    room_name = labels.get(case.admission.room_category, case.admission.room_category.replace("_", " ").capitalize())
    covered = current.payable > 0
    assumptions = _assumptions(basis)

    if not room_lines:
        note = f"You will pay about ₹{current.patient_share:,} yourself; the rest is billed to your insurer."
        return _result(claim_id, [], None, note, covered, [_summary(current.patient_share, current.payable)], basis, None, assumptions)

    rate = room_lines[0].amount // room_lines[0].quantity
    options = room_scenarios(case, TARIFF, room_name)

    lowest = min(o.patient_pays for o in options)
    best = max((o for o in options if o.patient_pays == lowest), key=lambda o: o.rate)
    room_rule = next((r for r in case.policy.rules if r.type == RuleType.ROOM_RENT_CAP), None)
    over_cap = room_rule is not None and any(d.rule_id == room_rule.rule_id for d in current.deductions)
    recommended = best if over_cap and not best.current and best.saving > 0 else None
    clause = f" (clause {room_rule.clause_id})" if room_rule else ""

    clause_text = f"Clause {room_rule.clause_id}: \"{room_rule.clause_text}\"" if room_rule else None
    if not covered:
        note = "Your policy does not cover this treatment, so the whole bill would be yours. A member of staff will talk you through it."
    elif recommended:
        note = (
            f"Your {room_name.lower()} costs ₹{rate:,} a day, which is above your policy's room limit{clause}. "
            "When the room costs more than the limit, the insurer also pays less of the doctor, nursing and "
            f"operation theatre charges. In this room you would pay about ₹{current.patient_share:,} yourself. "
            f"In a {recommended.room.lower()} at ₹{recommended.rate:,} a day you would pay about "
            f"₹{recommended.patient_pays:,}, saving about ₹{recommended.saving:,}. "
            + ("These figures assume the whole stay is in one room; if you move, only the days after the move change."
               if basis == "admission_estimate" else
               "This is worked out on the final bill, to explain your share; days already spent are not repriced.")
        )
    else:
        note = (
            f"Your {room_name.lower()} at ₹{rate:,} a day is within your policy's room limit, so the room adds "
            f"nothing to your share. You would pay about ₹{current.patient_share:,} yourself."
        )

    source = "Rules engine (what_if_room)" + (f", policy clause {room_rule.clause_id}" if room_rule else "")
    findings = [
        Finding(
            issue=f"{o.room} at ₹{o.rate:,} a day" + (" (current)" if o.current else ""),
            evidence=f"Patient pays ₹{o.patient_pays:,}; insurer pays ₹{o.insurer_pays:,}"
            + ("" if o.current else f"; {'saves' if o.saving >= 0 else 'costs'} ₹{abs(o.saving):,}"),
            source=source,
        )
        for o in options
    ]
    if room_rule:
        findings.append(Finding(issue="Room clause", evidence=room_rule.clause_text, source=f"Policy clause {room_rule.clause_id}"))
    return _result(claim_id, options, recommended, note, covered, findings, basis, clause_text, assumptions)


def _assumptions(basis: str) -> list[str]:
    first = ("Estimate at admission, on the bill as planned; the final bill can differ." if basis == "admission_estimate"
             else "Worked out on the final bill.")
    return [
        first,
        "Room options are the hospital's tariff, not live bed availability.",
        "Each option prices the whole stay as if every day had been in that room; a move changes charges only from the day of the move.",
        "Amounts come from the rules engine applying the policy's own clauses; the insurer's final decision may still differ.",
    ]


def _summary(patient: int, insurer: int) -> Finding:
    return Finding(issue="Cost split", evidence=f"Patient pays ₹{patient:,}; insurer pays ₹{insurer:,}", source="Rules engine")


def _result(
    claim_id: str, options: list[RoomOption], recommended: RoomOption | None, note: str, covered: bool, findings: list[Finding],
    basis: str = "admission_estimate", clause: str | None = None, assumptions: list[str] | None = None,
) -> AgentResult:
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision=("switch_room" if recommended else "keep_room") if covered else "mentions_denial",
            confidence=1.0,
            findings=findings,
            next="human_approval" if covered else HUMAN,  # staff check every note before the patient gets it
        ),
        data=PatientNote(options=options, recommended=recommended, note=note, basis=basis, clause=clause, assumptions=assumptions or []),
    )
