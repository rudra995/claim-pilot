"""Patient agent: a plain-language cost note with room advice, for staff to check and hand over (design sections 4, 10).

The room advisor prices the same stay in each room on the hospital's tariff with the rules engine's
`what_if_room`, and recommends the dearest room that costs the patient no more than the cheapest:
the best room the policy fully supports. Every amount comes from the engine; the note is built in
code. It never mentions fraud. A note that tells the patient the claim isn't covered goes to a person.
"""

from pydantic import BaseModel

from backend.agents.base import HUMAN, AgentResult
from backend.rules.engine import assess, what_if_room
from backend.schemas import AgentOutput, BillCategory, Case, Finding, RuleType
from data.generator.admissions import ROOMS

AGENT = "patient"
TARIFF = ROOMS  # the hospital's rooms: slug, label, rate per day


class RoomOption(BaseModel):
    room: str
    rate: int  # per day
    patient_pays: int
    insurer_pays: int
    saving: int  # against the current room; negative = costs the patient more
    current: bool


class PatientNote(BaseModel):
    options: list[RoomOption]  # dearest first
    recommended: RoomOption | None  # None: the current room is already the best value
    note: str


def run(claim_id: str, case: Case) -> AgentResult:
    room_lines = [line for line in case.bill_lines if line.category == BillCategory.ROOM]
    current = assess(case)
    labels = {slug: label for slug, label, _ in TARIFF}
    room_name = labels.get(case.admission.room_category, case.admission.room_category.replace("_", " ").capitalize())
    covered = current.payable > 0

    if not room_lines:
        note = f"You will pay about ₹{current.patient_share:,} yourself; the rest is billed to your insurer."
        return _result(claim_id, [], None, note, covered, [_summary(current.patient_share, current.payable)])

    rate = room_lines[0].amount // room_lines[0].quantity
    options = [RoomOption(room=room_name, rate=rate, patient_pays=current.patient_share, insurer_pays=current.payable, saving=0, current=True)]
    for slug, label, tariff_rate in TARIFF:
        if slug == case.admission.room_category or tariff_rate == rate:
            continue
        priced = what_if_room(case, tariff_rate)
        options.append(RoomOption(
            room=label, rate=tariff_rate, patient_pays=priced.patient_share, insurer_pays=priced.payable,
            saving=current.patient_share - priced.patient_share, current=False,
        ))
    options.sort(key=lambda o: o.rate, reverse=True)

    lowest = min(o.patient_pays for o in options)
    best = max((o for o in options if o.patient_pays == lowest), key=lambda o: o.rate)
    recommended = None if best.current or best.saving <= 0 else best
    room_rule = next((r for r in case.policy.rules if r.type == RuleType.ROOM_RENT_CAP), None)
    clause = f" (clause {room_rule.clause_id})" if room_rule else ""

    if not covered:
        note = "Your policy does not cover this treatment, so the whole bill would be yours. A member of staff will talk you through it."
    elif recommended:
        note = (
            f"Your {room_name.lower()} costs ₹{rate:,} a day, which is above your policy's room limit{clause}. "
            "When the room costs more than the limit, the insurer also pays less of the doctor, nursing and "
            f"operation theatre charges. In this room you would pay about ₹{current.patient_share:,} yourself. "
            f"In a {recommended.room.lower()} at ₹{recommended.rate:,} a day you would pay about "
            f"₹{recommended.patient_pays:,}, saving about ₹{recommended.saving:,}."
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
    return _result(claim_id, options, recommended, note, covered, findings)


def _summary(patient: int, insurer: int) -> Finding:
    return Finding(issue="Cost split", evidence=f"Patient pays ₹{patient:,}; insurer pays ₹{insurer:,}", source="Rules engine")


def _result(
    claim_id: str, options: list[RoomOption], recommended: RoomOption | None, note: str, covered: bool, findings: list[Finding]
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
        data=PatientNote(options=options, recommended=recommended, note=note),
    )
