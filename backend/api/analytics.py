"""The dashboard's numbers, worked out from the claims database (refinement of phase 12).

Everything here is measured from what the system recorded: the claim exchange and the audit log.
Rupee totals and rates go through the rules engine's helpers. The offline benchmark, if it has
been run, is attached separately and labelled as synthetic, so nobody mistakes it for live data.

Metric definitions (each stage of a claim, pre-auth, enhancement or discharge, counts once):

- first answer: the insurer's first message in that stage: APPROVE, PARTIAL_APPROVE, QUERY or DENY
- approved in full first time: the first answer is APPROVE
- part-approved first time: the first answer is PARTIAL_APPROVE (counted separately, never as "approved")
- query-free first answer: the first answer is not a QUERY
- deductions: those in the insurer's last decision in each stage, split by the rules engine into the
  part the policy supports (policy-backed) and the part it doesn't (disputed)
- recovered by appeal: how much more the insurer approved in its last decision than in its first
"""

import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.api import store
from backend.db import audit
from backend.exchange import messages as exchange
from backend.rules.engine import assess, excess, reconcile_payment, share, total
from backend.schemas import ClaimState, Final, Query, Sender, Stage, Submit

BENCHMARK = Path("reports/benchmark.json")
RISK_BINS = [(0.0, 0.2, "0–20%"), (0.2, 0.4, "20–40%"), (0.4, 0.6, "40–60%"), (0.6, 0.8, "60–80%"), (0.8, 1.01, "80–100%")]
STATE_GROUPS = [  # plain names, in the order a claim moves
    ("Preparing", {ClaimState.ADMITTED, ClaimState.PREAUTH_DRAFT, ClaimState.RISK_CHECK, ClaimState.DISCHARGE_DRAFT}),
    ("Needs a person", {ClaimState.AWAITING_HUMAN}),
    ("With the insurer", {ClaimState.SUBMITTED, ClaimState.QUERY_RAISED}),
    ("In treatment", {ClaimState.IN_TREATMENT, ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED}),
    ("Denied", {ClaimState.DENIED}),
    ("Awaiting payment", {ClaimState.FINAL_APPROVED, ClaimState.SETTLED}),
    ("Closed", {ClaimState.CLOSED}),
]
ANSWERS = ["APPROVE", "PARTIAL_APPROVE", "QUERY", "DENY"]


def _kind(message) -> str:
    return message.decision.value if isinstance(message, Final) else message.type.value


def _seconds(start: str, end: str) -> float:
    parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))  # noqa: E731
    return (parse(end) - parse(start)).total_seconds()


def analytics(db_path: Path | str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    states = Counter(row["state"] for row in rows)
    first_answers: dict[str, Counter] = {stage.value: Counter() for stage in Stage}
    by_tpa: dict[str, Counter] = {}
    clause_amounts: Counter = Counter()
    policy_backed, disputed, recovered = [], [], []
    decisions: Counter = Counter()
    risks: list[float] = []
    prep: list[float] = []
    llm = Counter()
    rechecks = attachments = escalations = scripted = 0

    for row in rows:
        claim_id = row["claim_id"]
        events = audit.events(db_path, claim_id)
        thread = exchange.thread(db_path, claim_id)
        case = store.from_events(events)

        for stage in Stage:
            answers = [m for m in thread if m.stage == stage and m.sender == Sender.INSURER]
            if not answers:
                continue
            first = _kind(answers[0])
            first_answers[stage.value][first] += 1
            if stage == Stage.PREAUTH:
                by_tpa.setdefault(row["tpa_id"], Counter())[first] += 1
            decided = [m for m in answers if not isinstance(m, Query)]
            if not decided:
                continue
            last = decided[-1]
            cuts = [d for d in getattr(last, "deductions", []) if not d.item.startswith("Whole claim")]
            for d in cuts:
                clause_amounts[d.clause] += d.amount
            submit = next((m for m in thread if m.stage == stage and isinstance(m, Submit)), None)
            if cuts and case is not None and submit is not None:
                split = reconcile_payment(assess(case), submit.requested_amount, ((d.clause, d.item, d.amount) for d in cuts), last.approved_amount, last.approved_amount)
                policy_backed.append(split.valid_total)
                disputed.append(total(c.recoverable for c in split.clauses))
            recovered.append(excess(getattr(last, "approved_amount", 0), getattr(decided[0], "approved_amount", 0)))

        for event in events:
            action = event["action"]
            if action == "human_decision":
                decisions[event["output"]["decision"]] += 1
                scripted += event["actor"].startswith("human:Demo seed")
            elif action == "recheck":
                rechecks += 1
            elif action == "document_attached":
                attachments += 1
            elif action == "escalate":
                escalations += 1
            elif action == "llm_call":
                llm["template" if event["output"].get("error") else "live"] += 1
        risk = next((e["output"]["data"]["risk"] for e in reversed(events) if e["action"] == "agent_output" and e["actor"] == "denial_risk"), None)
        if risk is not None:
            risks.append(risk)
        opened = next((e["created_at"] for e in events if e["action"] == "open_claim"), None)
        gate = next((e["created_at"] for e in events if e["action"] == "transition" and e["output"]["to"] == ClaimState.AWAITING_HUMAN), None)
        if opened and gate:
            prep.append(_seconds(opened, gate))

    pre = first_answers[Stage.PREAUTH.value]
    every = sum((c for c in first_answers.values()), Counter())
    return {
        "claims": len(rows),
        "kpis": {
            "active_claims": sum(n for s, n in states.items() if s != ClaimState.CLOSED),
            "waiting_for_a_person": states.get(ClaimState.AWAITING_HUMAN, 0),
            "preauth_answers": sum(pre.values()),
            "approved_in_full_first_time": share(pre["APPROVE"], sum(pre.values())),
            "part_approved_first_time": share(pre["PARTIAL_APPROVE"], sum(pre.values())),
            "query_free_first_answer": share(sum(n for k, n in every.items() if k != "QUERY"), sum(every.values())),
            "requested_total": total(row["requested"] or 0 for row in rows),
            "approved_total": total(row["approved"] or 0 for row in rows),
            "approved_share_of_requested": share(total(row["approved"] or 0 for row in rows), total(row["requested"] or 0 for row in rows)),
            "median_package_seconds": round(statistics.median(prep), 1) if prep else None,
        },
        "pipeline": [{"label": name, "value": sum(states.get(s, 0) for s in group)} for name, group in STATE_GROUPS],
        "first_answers": [
            {"label": {"preauth": "Pre-auth", "enhancement": "Enhancement", "discharge": "Discharge"}[stage], **{k: counts.get(k, 0) for k in ANSWERS}}
            for stage, counts in first_answers.items() if counts
        ],
        "first_answers_by_tpa": [{"label": tpa, **{k: counts.get(k, 0) for k in ANSWERS}} for tpa, counts in sorted(by_tpa.items())],
        "deductions_by_clause": [
            {"label": clause if not clause[:1].isdigit() else f"Clause {clause}", "value": amount} for clause, amount in clause_amounts.most_common(8)
        ],
        "deductions_split": {"policy_backed": total(policy_backed), "disputed": total(disputed), "recovered_by_appeal": total(recovered)},
        "oversight": {
            "decisions": [{"label": label, "value": decisions.get(key, 0)} for key, label in (
                ("approve", "Approved as drafted"), ("edit", "Edited, then approved"), ("reject", "Held back"), ("accept", "Accepted insurer's decision"))],
            "rechecks": rechecks, "documents_attached": attachments, "escalations": escalations, "scripted_decisions": scripted,
        },
        "risk_histogram": [{"label": label, "value": sum(lo <= r < hi for r in risks)} for lo, hi, label in RISK_BINS],
        "llm": {"live": llm["live"], "template": llm["template"]},
        "benchmark": benchmark_summary(),
    }


def benchmark_summary(path: Path = BENCHMARK) -> dict[str, Any] | None:
    """The offline benchmark's headline rows, if it has been run. Synthetic data, simulated insurer."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data["summary"]
    modes = list(data["run"]["modes"])

    def row(key: str, insurer: str = "standard") -> dict[str, Any]:
        return {mode: summary[f"{insurer}/eval/{mode}"][key] for mode in modes}

    return {
        "label": "Offline benchmark: synthetic cases, simulated insurer",
        "generated": data["run"]["started"],
        "cases": len(data["run"]["cases"]["eval"]),
        "modes": modes,
        "metrics": [
            {"key": "query_rounds_total", "label": "Insurer queries", "unit": "count", "values": row("query_rounds_total"), "better": "lower"},
            {"key": "issues_found_before_sending", "label": "Planted issues caught before sending", "unit": "count", "values": row("issues_found_before_sending"), "better": "higher",
             "of": summary[f"standard/eval/{modes[0]}"]["issues_expected"]},
            {"key": "preauth_first_pass_full", "label": "Pre-auth approved in full, first answer", "unit": "share", "values": row("preauth_first_pass_full"), "better": "higher"},
            {"key": "simulated_recovered_rupees", "label": "Simulated recovery by appeal (held-out insurer)", "unit": "rupees", "values": row("simulated_recovered_rupees", "held_out"), "better": "higher"},
        ],
        "notes": [
            "Rules and claimpilot share the same document checklist, so they catch the same missing records.",
            "The claimpilot mode adds evidence-checked appeals; the simulated insurer judges them with the same evidence check.",
        ],
    }
