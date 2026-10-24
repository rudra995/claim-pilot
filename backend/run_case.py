"""Run one fixture case through the admission flow and print what each agent did.

    python -m backend.run_case fixtures/case1.json [--db claimpilot.db] [--approve-as "Your Name"]

With --approve-as, you are the billing officer: every message the hospital drafts is approved in
your name and sent, and the negotiation with the simulated insurer is printed round by round,
until the claim is decided or needs a decision other than approval. Use a fresh --db to run the
same case twice, or the insurer's fraud screen will see a duplicate claim.
"""

import argparse
import sys
from pathlib import Path

from backend.agents.approval import Decision, decide
from backend.agents.orchestrator import ADMISSION_AGENTS, RunResult, pending_draft, run_admission
from backend.db.db import DEFAULT_DB_PATH
from backend.exchange import messages as exchange
from backend.negotiation import play_round
from backend.schemas import MAX_ROUNDS, ClaimState, Case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--approve-as", metavar="NAME", help="approve each drafted message as this officer and negotiate")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # amounts print with ₹

    case = Case.model_validate_json(args.case.read_text(encoding="utf-8"))
    result = run_admission(case, args.db)

    print(f"{result.claim_id}  {case.title}")
    _print_agents(result, ADMISSION_AGENTS)
    print(f"State: {result.state}")
    if "preauth" in result.results:
        package = result.results["preauth"].data
        print(f"Pre-auth requested: ₹{package.requested_amount:,} (cost model); justification by {package.justification_by}")
        print(f"  {package.justification}")
    _print_flags(result)

    if args.approve_as:
        _negotiate(args.db, case, result, args.approve_as)


def _negotiate(db: Path, case: Case, result: RunResult, officer: str) -> None:
    shown = 0
    while result.state == ClaimState.AWAITING_HUMAN and pending_draft(db, result.claim_id) is not None:
        decide(db, result.claim_id, Decision(decision="approve", officer=officer))
        result = play_round(db, result.claim_id, case)
        messages = exchange.thread(db, result.claim_id)
        print()
        for message in messages[shown:]:
            _print_message(message)
        shown = len(messages)
        _print_agents(result, list(result.results))
        print(f"State: {result.state}")
        _print_flags(result)
    if result.state == ClaimState.AWAITING_HUMAN:
        print(f"Waiting for a person: accept the insurer's decision or reject (at most {MAX_ROUNDS} rounds).")


def _print_agents(result: RunResult, names: list[str]) -> None:
    for name in names:
        if name not in result.results:
            print(f"  {name:<16} did not run")
            continue
        output = result.results[name].output
        print(f"  {name:<16} {output.decision:<24} confidence {output.confidence:.2f}  next: {output.next}")
        for finding in output.findings:
            print(f"      - {finding.issue}: {finding.evidence}  [{finding.source}]")


def _print_flags(result: RunResult) -> None:
    for flag in result.review_flags:
        print(f"Needs a person: {flag}")
    for name in result.failed:
        print(f"Escalated: {name} failed twice")


def _print_message(message) -> None:
    body = message.model_dump(exclude={"claim_id", "stage", "round", "type", "package"}, mode="json")
    print(f"[round {message.round}] {message.sender.value:>8} {message.type}")
    for key, value in body.items():
        if key == "deductions":
            for d in value:
                print(f"      - {d['item']}: ₹{d['amount']:,}  [clause {d['clause']}]")
        elif isinstance(value, int):
            print(f"    {key}: ₹{value:,}")
        else:
            print(f"    {key}: {value}")


if __name__ == "__main__":
    main()
