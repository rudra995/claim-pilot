"""Three-mode benchmark on frozen synthetic cases, under two simulated insurer behaviours.

    python -m backend.bench.run            (writes reports/benchmark.json and reports/BENCHMARK.md)

Modes (same cases, same documents, same insurer, same approval gate in each):

    basic       the package as first assembled goes out; queries are answered by fetching the record
                asked for; insurer cuts are accepted, never appealed
    rules       a deterministic document checklist and the rules engine run before sending; missing
                records found are fetched first; only cuts the policy wording contradicts are appealed
    claimpilot  everything in rules, plus the denial-risk model and evidence-checked appeals: a
                medical-necessity cut is appealed only when the records hold findings that support it

Fairness rules for the comparison:

- Every mode can fetch the same records from the hospital's record store, at the same assumed effort
  (RETRIEVAL_MINUTES each). A mode that doesn't look first fetches only when the insurer asks.
- No mode is given evidence the others lack. Labels and the TPA's hidden habits stay in this harness;
  the hospital agents never see them.
- Pre-auth sees only admission-time records (backend/stages.py) in every mode.
- The benchmark reviewer is a stand-in for the billing officer: it approves each draft unchanged,
  rejects an appeal only where the mode doesn't appeal, and otherwise accepts the insurer's
  decision. It exists only here; the application never approves anything without a person.

Every rupee aggregate comes from the rules engine's helpers. "Simulated recovery" is what the
simulated insurer paid more after appeals; it is not evidence about real insurers.
"""

import json
import os
import platform
import shutil
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import llm
from backend.agents import orchestrator
from backend.agents.approval import Decision, decide
from backend.agents.hospital.denial_risk import live_features
from backend.agents.hospital.intake import EXPECTED, Reading
from backend.agents.orchestrator import pending_draft, recheck, run_admission
from backend.db import audit
from backend.exchange import messages as exchange
from backend.models.features import FLAGS
from backend.models.fixes_map import FIXES
from backend.negotiation import play_round
from backend.remediation import attach
from backend.rules.engine import assess, excess, reconcile_payment, share, total
from backend.schemas import Case, ClaimState, Document, DocumentType, Final, PartialApprove, Query, Stage, claim_message_adapter
from backend.stages import due_at
from data.generator.admissions import ADMISSION_REASON, Problem, _documents, required_documents
from data.generator.generate import generate
from data.generator.procedures import BY_SLUG
from data.generator.render import TITLES, render_pdf

MODES = ("basic", "rules", "claimpilot")
INSURERS = ("standard", "held_out")
PER_PROBLEM = 8  # eval cases per planted problem, from the model's time-based test split
RETRIEVAL_MINUTES = 10  # assumed staff effort to fetch one record (an assumption, not a measurement)
REVIEW_MINUTES = 3  # assumed staff effort per approval gate (an assumption, not a measurement)
REVIEWER = "Benchmark reviewer (simulated; approves drafts unchanged; never used by the app)"
OUT = Path("reports")
NECESSITY_CLAUSE = "Medical necessity"

EXPECTED_FLAG = {
    Problem.ROOM_OVER_CAP: "room_over_cap", Problem.NON_PAYABLES: "non_payables_billed",
    Problem.SUB_LIMIT_EXCEEDED: "sub_limit_exceeded", Problem.WAITING_PERIOD_BREACHED: "waiting_period_breached",
    Problem.CODING_MISMATCH: "coding_mismatch",
}


@dataclass
class BenchCase:
    case: Case
    group: str  # "eval", "tuning" (the demo cases the system was built on) or "challenge"
    problem: str
    expected_flags: set[str]
    records: dict[str, Document] = field(default_factory=dict)  # doc type -> a record the hospital holds but didn't attach
    appeal_warranted: bool = True  # label: was admission clinically justified (a necessity cut is wrong)
    truth: dict[str, dict] = field(default_factory=dict)  # doc id -> true field values, for scoring the reader only


# ---- Frozen cases ------------------------------------------------------------------------------


def frozen_cases(folder: Path) -> list[BenchCase]:
    pairs = generate()
    ordered = sorted(pairs, key=lambda p: p[0].case.admission.admit_date)  # the model's time split
    test = ordered[int(len(ordered) * 0.8):]
    picked: list[BenchCase] = []
    counts: Counter = Counter()
    for generated, label in sorted(test, key=lambda p: p[0].case.admission.admission_id):
        if counts[generated.problem] >= PER_PROBLEM:
            continue
        counts[generated.problem] += 1
        picked.append(_eval_case(generated, folder))
    return picked + _tuning_cases(folder) + _challenge_cases(folder)


def _render(case: Case, folder: Path) -> Case:
    docs = []
    for doc in case.documents:
        path = folder / case.admission.admission_id / f"{doc.doc_id}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_pdf(doc, case, None))
        docs.append(doc.model_copy(update={"file_path": path.as_posix()}))
    return case.model_copy(update={"documents": docs})


def _eval_case(generated, folder: Path) -> BenchCase:
    case = _render(generated.case, folder)
    expected: set[str] = set()
    records: dict[str, Document] = {}
    truth = {doc.doc_id: doc.fields for doc in case.documents}
    if generated.problem in EXPECTED_FLAG:
        expected.add(EXPECTED_FLAG[generated.problem])
    elif generated.problem == Problem.MISSING_DOCUMENT:
        if generated.detail == ADMISSION_REASON:
            expected.add("admission_reason_missing")
        else:
            expected.add(f"missing_{generated.detail}")
            procedure = BY_SLUG[case.admission.procedure]
            stay = (case.admission.discharge_date - case.admission.admit_date).days
            full = _documents(case.admission.admission_id, procedure, case.admission.icd10, stay, total(l.amount for l in case.bill_lines), None)
            record = next(d for d in full if d.type.value == generated.detail).model_copy(update={"doc_id": f"{case.admission.admission_id}-R1"})
            path = folder / case.admission.admission_id / f"{record.doc_id}.pdf"
            path.write_bytes(render_pdf(record, case, None))
            records[generated.detail] = record.model_copy(update={"file_path": path.as_posix(), "fields": {}})
            truth[record.doc_id] = record.fields
    return BenchCase(case=case, group="eval", problem=generated.problem.value, expected_flags=expected, records=records, truth=truth)


def _fixture(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


def _tuning_cases(folder: Path) -> list[BenchCase]:
    xray = Document(doc_id="ADM-0002-R1", type=DocumentType.XRAY_REPORT, file_path="fixtures/remediation/ADM-0002-xray-report.pdf")
    return [
        BenchCase(case=_fixture("case1.json"), group="tuning", problem="clean", expected_flags=set()),
        BenchCase(case=_fixture("case2.json"), group="tuning", problem="missing_document", expected_flags={"missing_xray_report", "room_over_cap", "non_payables_billed"}, records={"xray_report": xray}),
        BenchCase(case=_fixture("case3.json"), group="tuning", problem="missing_document", expected_flags={"admission_reason_missing"}),
    ]


def _challenge_cases(folder: Path) -> list[BenchCase]:
    """Case 3 with its admission records rewritten: the cut is right when the records show nothing."""
    base = _fixture("case3.json")
    variants = {
        "irrelevant": ({"admission_note": {"chief_complaint": "Loose stools for 2 days", "provisional_diagnosis": "Acute gastroenteritis", "planned_treatment": "Monitoring"},
                        "lab_report": {"test": "Serum electrolytes and renal function", "finding": "Within normal limits"}}, False),
        "contradictory": ({"admission_note": {"chief_complaint": "Loose stools for 2 days", "provisional_diagnosis": "Acute gastroenteritis",
                                              "vitals": "Vitals stable, no dehydration", "assessment": "Could be managed as an outpatient"}}, False),
    }
    out = []
    for name, (fields_by_type, warranted) in variants.items():
        docs = [d.model_copy(update={"fields": fields_by_type[d.type.value]}) if d.type.value in fields_by_type else d for d in base.documents]
        case = _reidentify(base.model_copy(update={"documents": docs}), f"case3-{name}", f"ADM-03{name[:2].upper()}")
        rendered = _render(case, folder)
        out.append(BenchCase(case=rendered, group="challenge", problem=f"necessity_{name}", expected_flags={"admission_reason_missing"}, appeal_warranted=warranted))
    return out


def _reidentify(case: Case, case_id: str, admission_id: str) -> Case:
    """The same case under new ids, so it is a separate admission (not a duplicate claim) in the database."""
    old = case.admission.admission_id
    data = json.loads(case.model_dump_json().replace(old, admission_id).replace(case.patient.patient_id, f"PAT-{admission_id}").replace(case.policy.policy_id, f"POL-{admission_id}"))
    return Case.model_validate({**data, "case_id": case_id})


# ---- Driving one case through one mode --------------------------------------------------------


def _latest(db: Path, claim_id: str, actor: str) -> dict | None:
    out = None
    for event in audit.events(db, claim_id):
        if event["action"] == "agent_output" and event["actor"] == actor:
            out = event["output"]
    return out


def _flags_now(db: Path, claim_id: str, case: Case, stage: Stage, mode: str) -> set[str]:
    """What the mode's own checks say before sending: nothing for basic, checklist flags otherwise."""
    if mode == "basic":
        return set()
    if mode == "claimpilot":
        risk = _latest(db, claim_id, "denial_risk")
        return {item["flag"] for item in risk["data"]["items"]} if risk else set()
    reading = orchestrator._reading(db, case)
    return {flag for flag, value in live_features(case, reading, stage).items() if flag in FIXES and value}


def _fetch(db: Path, claim_id: str, bc: BenchCase, case: Case, doc_type: str, log: dict) -> Case:
    record = bc.records.pop(doc_type, None)
    if record is None:
        return case
    case, attached, _ = attach(db, claim_id, case, DocumentType(doc_type), f"{doc_type}.pdf", Path(record.file_path).read_bytes(), REVIEWER)
    bc.truth[attached.doc_id] = bc.truth.get(record.doc_id, {})  # the reader sees the new id; the score needs its truth
    log["retrievals"].append(doc_type)
    return case


def _remediate_before_sending(db: Path, claim_id: str, bc: BenchCase, case: Case, stage: Stage, mode: str, log: dict) -> Case:
    flags = _flags_now(db, claim_id, case, stage, mode)
    log["flags"][stage.value] = sorted(flags)
    wanted = [f.removeprefix("missing_") for f in flags if f.startswith("missing_") and f.removeprefix("missing_") in bc.records]
    if not wanted:
        return case
    for doc_type in wanted:
        case = _fetch(db, claim_id, bc, case, doc_type, log)
    recheck(db, claim_id, case, "Records fetched before sending")
    return case


def _approve(db: Path, claim_id: str, log: dict) -> None:
    decide(db, claim_id, Decision(decision="approve", officer=REVIEWER, draft_hash=pending_draft(db, claim_id)["hash"]))
    log["gates"] += 1


def run_case(db: Path, bc: BenchCase, mode: str) -> dict[str, Any]:
    bc = BenchCase(**{**bc.__dict__, "records": dict(bc.records), "truth": dict(bc.truth)})  # each run works on its own copy
    case = bc.case
    log: dict[str, Any] = {"retrievals": [], "flags": {}, "gates": 0, "escalations": 0, "appeals": [], "prep_seconds": {}}
    started = time.perf_counter()
    claim_id = run_admission(case, db).claim_id
    log["prep_seconds"]["preauth"] = round(time.perf_counter() - started, 3)
    case = _remediate_before_sending(db, claim_id, bc, case, Stage.PREAUTH, mode, log)

    for _ in range(14):
        state = orchestrator.claim_state(db, claim_id)
        pending = pending_draft(db, claim_id)
        if state == ClaimState.AWAITING_HUMAN and pending:
            message = pending["message"]
            if message["type"] == "APPEAL":
                draft = _latest(db, claim_id, "appeal")["data"]
                kinds = {c["kind"] for c in draft["contested"]}
                if mode == "basic" or (mode == "rules" and kinds != {"policy"}):
                    decide(db, claim_id, Decision(decision="reject", officer=REVIEWER, reason=f"{mode} mode doesn't appeal this"))
                    log["gates"] += 1
                    continue
                log["appeals"].append({"stage": message["stage"], "kinds": sorted(kinds), "disputed": draft["disputed_total"]})
            elif message["type"] == "RESPONSE" and _latest(db, claim_id, "query_responder")["output"]["decision"] == "document_missing":
                asked = exchange.latest(db, claim_id)
                wanted = [t for t in list(bc.records) if TITLES[DocumentType(t)].lower() in asked.missing.lower()]
                if wanted:
                    for doc_type in wanted:
                        case = _fetch(db, claim_id, bc, case, doc_type, log)
                    recheck(db, claim_id, case, "Record fetched for the insurer's query")
                    continue
            _approve(db, claim_id, log)
            play_round(db, claim_id, case)
        elif state == ClaimState.AWAITING_HUMAN:
            log["escalations"] += 1
            last = exchange.latest(db, claim_id)
            if last is not None and last.sender.value == "insurer" and not isinstance(last, Query):
                decide(db, claim_id, Decision(decision="accept", officer=REVIEWER))
                log["gates"] += 1
            else:
                break
        elif state in (ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED):
            if orchestrator.next_stage(db, claim_id) != ClaimState.IN_TREATMENT:
                break
            started = time.perf_counter()
            orchestrator.discharge(db, claim_id, case)
            log["prep_seconds"]["discharge"] = round(time.perf_counter() - started, 3)
            case = _remediate_before_sending(db, claim_id, bc, case, Stage.DISCHARGE, mode, log)
        else:
            break
    return _outcome(db, claim_id, bc, case, mode, log)


def _outcome(db: Path, claim_id: str, bc: BenchCase, case: Case, mode: str, log: dict) -> dict[str, Any]:
    thread = exchange.thread(db, claim_id)
    stages: dict[str, Any] = {}
    for stage in (Stage.PREAUTH, Stage.DISCHARGE):
        answers = [m for m in thread if m.stage == stage and m.sender.value == "insurer"]
        if not answers:
            continue
        decisions = [m for m in answers if not isinstance(m, Query)]
        first_decision, final = (decisions[0], decisions[-1]) if decisions else (None, None)
        eligible = 0
        if isinstance(first_decision, (PartialApprove, Final)) and first_decision.deductions:
            submit = next(m for m in thread if m.stage == stage and m.type == "SUBMIT")
            cuts = [d for d in first_decision.deductions if not d.item.startswith("Whole claim")]  # a denial isn't a disputable cut
            rec = reconcile_payment(assess(case), submit.requested_amount, ((d.clause, d.item, d.amount) for d in cuts), first_decision.approved_amount, first_decision.approved_amount)
            eligible = total(c.recoverable for c in rec.clauses if bc.appeal_warranted or not c.clause.startswith(NECESSITY_CLAUSE))
        first_amount = getattr(first_decision, "approved_amount", 0) if first_decision else 0
        final_amount = getattr(final, "approved_amount", 0) if final else 0
        stages[stage.value] = {
            "first_answer": _kind(answers[0]),
            "queries": sum(isinstance(m, Query) for m in answers),
            "rounds": answers[-1].round,
            "final": _kind(final) if final else None,
            "eligible_disputed": eligible,
            "recovered": excess(final_amount, first_amount),
            "approved": final_amount,
        }
    flags_raised = set(log["flags"].get("preauth", [])) | set(log["flags"].get("discharge", []))
    reading = orchestrator._reading(db, case)
    necessity_appeals = [a for a in log["appeals"] if "necessity" in a["kinds"]]
    events = audit.events(db, claim_id)
    llm_events = [e for e in events if e["action"] == "llm_call"]
    appeal_outputs = [e["output"]["data"] for e in events if e["action"] == "agent_output" and e["actor"] == "appeal"]
    necessity_held = [h["why"] for d in appeal_outputs for h in d.get("held_back", []) if h["kind"] == "necessity"]
    return {
        "case_id": bc.case.case_id, "admission_id": bc.case.admission.admission_id, "group": bc.group, "problem": bc.problem,
        "tpa": bc.case.admission.tpa_id, "mode": mode, "state": orchestrator.claim_state(db, claim_id).value, "stages": stages,
        "expected_flags": sorted(bc.expected_flags), "flags_raised": sorted(flags_raised),
        "found": sorted(bc.expected_flags & flags_raised), "missed": sorted(bc.expected_flags - flags_raised),
        "false_flags": sorted(flags_raised - bc.expected_flags),
        "retrievals": log["retrievals"], "gates": log["gates"], "escalations": log["escalations"], "appeals": log["appeals"],
        "appeals_correct": sum(1 for a in log["appeals"] if "necessity" not in a["kinds"] or bc.appeal_warranted),
        "appeals_incorrect": 0 if bc.appeal_warranted else len(necessity_appeals),
        "necessity_appeals": len(necessity_appeals), "necessity_held_back": len(necessity_held), "held_back_why": necessity_held,
        "prep_seconds": log["prep_seconds"], "llm_calls": len(llm_events), "llm_unavailable": sum(1 for e in llm_events if e["output"].get("error")),
        "extraction": _extraction(bc.truth or {d.doc_id: d.fields for d in bc.case.documents}, reading),
    }


def _kind(message) -> str:
    return message.decision.value if isinstance(message, Final) else message.type.value


def _extraction(truth: dict[str, dict], reading: Reading) -> dict[str, int]:
    """Fields read against the generator's ground truth (scored here only; the reader never sees it)."""
    counts = Counter()
    for doc in reading.documents:
        for name in EXPECTED.get(doc.type, []):
            expected = truth.get(doc.doc_id, {}).get(name)
            field = doc.fields.get(name)
            if expected in (None, ""):
                counts["correctly_blank" if field is None or field.value in (None, "") else "invented"] += 1
            elif field is None or field.value in (None, "") or field.basis in ("conflict", "unreadable"):
                counts["abstained"] += 1
            elif str(field.value).strip().lower() == str(expected).strip().lower():
                counts["correct"] += 1
            else:
                counts["wrong"] += 1
    return dict(counts)


# ---- Running and summarising ------------------------------------------------------------------


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def stage_rows(stage: str) -> list[dict]:
        return [r["stages"][stage] for r in rows if stage in r["stages"]]

    pre = stage_rows("preauth")
    every = [s for r in rows for s in r["stages"].values()]
    extraction = Counter()
    for r in rows:
        extraction.update(r["extraction"])
    prep = [r["prep_seconds"]["preauth"] for r in rows if "preauth" in r["prep_seconds"]]
    return {
        "cases": len(rows),
        "preauth_first_pass_full": share(sum(s["first_answer"] == "APPROVE" for s in pre), len(pre)),
        "preauth_first_pass_partial": share(sum(s["first_answer"] == "PARTIAL_APPROVE" for s in pre), len(pre)),
        "preauth_query_free_first_response": share(sum(s["first_answer"] != "QUERY" for s in pre), len(pre)),
        "all_stages_query_free_first_response": share(sum(s["first_answer"] != "QUERY" for s in every), len(every)),
        "query_rounds_total": sum(s["queries"] for s in every),
        "issues_expected": sum(len(r["expected_flags"]) for r in rows),
        "issues_found_before_sending": sum(len(r["found"]) for r in rows),
        "issues_missed": sum(len(r["missed"]) for r in rows),
        "false_flags": sum(len(r["false_flags"]) for r in rows),
        "human_gates": sum(r["gates"] for r in rows),
        "escalations_to_a_person": sum(r["escalations"] for r in rows),
        "records_fetched": sum(len(r["retrievals"]) for r in rows),
        "appeals_sent": sum(len(r["appeals"]) for r in rows),
        "necessity_appeals_sent": sum(r["necessity_appeals"] for r in rows),
        # only the claimpilot mode decides necessity appeals on evidence; the others never argue necessity
        "necessity_cuts_held_back": sum(r["necessity_held_back"] for r in rows) if rows and rows[0]["mode"] == "claimpilot" else None,
        "appeals_correct": sum(r["appeals_correct"] for r in rows),
        "appeals_incorrect": sum(r["appeals_incorrect"] for r in rows),
        "eligible_disputed_rupees": total(s["eligible_disputed"] for s in every),
        "simulated_recovered_rupees": total(s["recovered"] for s in every),
        "approved_rupees": total(s["approved"] for s in every),
        "denied_stages": sum(s["final"] == "DENY" for s in every),
        "preauth_prep_seconds_median": round(statistics.median(prep), 3) if prep else None,
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "llm_unavailable": sum(r["llm_unavailable"] for r in rows),
        "extraction": dict(extraction),
        "assumed_staff_minutes": {
            "fetching_records": sum(len(r["retrievals"]) for r in rows) * RETRIEVAL_MINUTES,
            "reviewing_gates": sum(r["gates"] for r in rows) * REVIEW_MINUTES,
            "note": f"Assumptions, not measurements: {RETRIEVAL_MINUTES} min per record fetched, {REVIEW_MINUTES} min per approval gate.",
        },
    }


def run(out: Path = OUT) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    work = Path(tempfile.mkdtemp(prefix="claimpilot-bench-"))
    previous = {k: os.environ.get(k) for k in ("CLAIMPILOT_SIMULATOR", "CLAIMPILOT_UPLOADS")}
    os.environ["CLAIMPILOT_UPLOADS"] = str(work / "uploads")
    try:
        cases = frozen_cases(work / "docs")
        rows, failures = [], []
        for insurer in INSURERS:
            os.environ["CLAIMPILOT_SIMULATOR"] = insurer
            for mode in MODES:
                db = work / f"{insurer}-{mode}.db"
                for bc in cases:
                    try:
                        rows.append({**run_case(db, bc, mode), "insurer": insurer})
                    except Exception as error:  # a failed case is reported, never silently dropped
                        failures.append({"case_id": bc.case.case_id, "mode": mode, "insurer": insurer, "error": repr(error)[:300]})
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(work, ignore_errors=True)

    summary = {
        f"{insurer}/{group}/{mode}": summarise([r for r in rows if r["insurer"] == insurer and r["group"] == group and r["mode"] == mode])
        for insurer in INSURERS for group in ("eval", "tuning", "challenge") for mode in MODES
    }
    from backend.bench.challenge import FOLDER, score

    challenge = {"development": score(FOLDER), "held_out": score(FOLDER / "held_out")}
    result = {
        "challenge_documents": challenge,
        "run": {
            "started": started.isoformat(timespec="seconds"), "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
            "python": platform.python_version(), "llm": llm.status(probe=False), "reviewer": REVIEWER,
            "modes": MODES, "insurers": {"standard": "TPA habits the data was generated with", "held_out": "habits never used in training or tuning"},
            "cases": {group: sorted({bc.case.case_id for bc in cases if bc.group == group}) for group in ("eval", "tuning", "challenge")},
            "selection": f"Up to {PER_PROBLEM} admissions per planted problem from the model's time-based test split (latest 20% by admission date), by admission id.",
            "failures": failures, "excluded": [],
        },
        "summary": summary,
        "rows": rows,
    }
    out.mkdir(exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "BENCHMARK.md").write_text(markdown(result), encoding="utf-8")
    return result


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _rupees(value: int) -> str:
    return f"₹{value:,}"


def markdown(result: dict[str, Any]) -> str:
    run_info, summary = result["run"], result["summary"]
    lines = [
        "# ClaimPilot benchmark",
        "",
        f"Generated by `python -m backend.bench.run` on {run_info['started']} ({run_info['seconds']} s). "
        "Every number below was measured in that run on synthetic data, against a simulated insurer. "
        "None of it is evidence about real hospitals or insurers.",
        "",
        f"- LLM: {run_info['llm']['mode']} ({run_info['llm'].get('reason') or 'live model'}). With no model reachable, agents use their labelled templates; the LLM only writes prose in this system, never amounts or decisions.",
        f"- Reviewer: {run_info['reviewer']}.",
        f"- Cases: {len(run_info['cases']['eval'])} eval (held out from tuning), {len(run_info['cases']['tuning'])} tuning (the demo cases the system was built on), {len(run_info['cases']['challenge'])} appeal challenge variants. {run_info['selection']}",
        f"- Failures: {len(run_info['failures'])}. Exclusions: none.",
        "",
    ]
    metrics = [
        ("Pre-auth approved in full, first answer", "preauth_first_pass_full", _pct),
        ("Pre-auth part-approved, first answer", "preauth_first_pass_partial", _pct),
        ("Pre-auth first answer without a query", "preauth_query_free_first_response", _pct),
        ("Insurer queries (all stages)", "query_rounds_total", str),
        ("Planted issues found before sending", "issues_found_before_sending", str),
        ("Planted issues missed before sending", "issues_missed", str),
        ("Flags with no planted issue", "false_flags", str),
        ("Records fetched (remediation)", "records_fetched", str),
        ("Approval gates passed by the reviewer", "human_gates", str),
        ("Escalations to a person", "escalations_to_a_person", str),
        ("Appeals sent", "appeals_sent", str),
        ("Of which medical-necessity appeals", "necessity_appeals_sent", str),
        ("Necessity cuts not appealed (evidence too weak)", "necessity_cuts_held_back", lambda v: "n/a" if v is None else str(v)),
        ("Appeals sent that were wrong (label)", "appeals_incorrect", str),
        ("Eligible disputed amount (engine)", "eligible_disputed_rupees", _rupees),
        ("Simulated amount recovered by appeal", "simulated_recovered_rupees", _rupees),
        ("Stages ending in denial", "denied_stages", str),
        ("Median pre-auth package time (s, measured)", "preauth_prep_seconds_median", str),
        ("LLM calls / answered by a template instead", None, None),
    ]
    for insurer in ("standard", "held_out"):
        for group in ("eval", "challenge"):
            title = {"eval": "Eval cases", "challenge": "Appeal challenge (records rewritten so the cut is right)"}[group]
            lines += [f"## {title}, {insurer.replace('_', '-')} insurer", "", "| Metric | basic | rules | claimpilot |", "| --- | --- | --- | --- |"]
            for label, key, fmt in metrics:
                cells = []
                for mode in MODES:
                    s = summary[f"{insurer}/{group}/{mode}"]
                    cells.append(f"{s['llm_calls']} / {s['llm_unavailable']}" if key is None else fmt(s[key]))
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
            lines.append("")
    for name, label in (("development", "used while building the reader"), ("held_out", "written after the reader was frozen and never tuned on; re-scored once after a later findings/impression fix, same result")):
        c = result["challenge_documents"][name]
        failed = [f"{row['doc_id']}: {check['check']} (got {row['code_outcome']})" if check["check"].startswith("code") else f"{row['doc_id']}: {check['check']}"
                  for row in c["rows"] for check in row["checks"] if not check["passed"]]
        lines += [
            f"## Challenge documents, {name.replace('_', '-')} set ({label})",
            "",
            f"{c['documents']} independently written documents in other layouts: {c['passed']}/{c['checks']} checks passed; "
            f"fields {c['field_accuracy']['correct']}/{c['field_accuracy']['of']} correct; abstentions {c['abstention']['correct']}/{c['abstention']['of']} as expected."
            + (f" Misses: {'; '.join(failed)}." if failed else ""),
            "",
        ]
    tuning = summary["standard/tuning/claimpilot"]
    extraction = summary["standard/eval/claimpilot"]["extraction"]
    lines += [
        "## Document reading (eval cases, generator's own layout)",
        "",
        f"Expected fields: {extraction.get('correct', 0)} correct, {extraction.get('wrong', 0)} wrong, {extraction.get('abstained', 0)} abstained, "
        f"{extraction.get('correctly_blank', 0)} correctly left blank, {extraction.get('invented', 0)} invented. "
        "These documents use the hospital's own template, so this is the easy case; see the challenge-set results for other layouts.",
        "",
        f"Tuning cases (built on, so not evidence): {tuning['cases']} cases, {tuning['appeals_sent']} appeals, {_rupees(tuning['simulated_recovered_rupees'])} simulated recovery.",
        "",
        "## How to read this",
        "",
        "- The rules and claimpilot modes share the same deterministic checklist, so they find the same missing records; any first-pass gain over basic comes from that checklist, not from AI.",
        "- What the claimpilot mode adds is evidence-checked medical-necessity appeals: it appeals a cut only when the records hold findings that justify admission, and holds back when they don't (the challenge rows).",
        "- The simulated insurer uses the same evidence check as the hospital to judge appeals, so an appeal that passes the hospital's check will tend to win. Treat recovery as a consistency check, not a measured win rate.",
        "- Staff time is not measured here. The JSON gives assumed minutes (fetching records, reviewing gates) separately from measured package times.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    result = run()
    print(f"Wrote reports/benchmark.json and reports/BENCHMARK.md ({len(result['rows'])} runs, {len(result['run']['failures'])} failures)")
