"""Run one fixture case through the admission flow and print what each agent did.

    python -m backend.run_case fixtures/case1.json [--db claimpilot.db]
"""

import argparse
import sys
from pathlib import Path

from backend.agents.orchestrator import ADMISSION_AGENTS, run_admission
from backend.db.db import DEFAULT_DB_PATH
from backend.schemas import Case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # amounts print with ₹

    case = Case.model_validate_json(args.case.read_text(encoding="utf-8"))
    result = run_admission(case, args.db)

    print(f"{result.claim_id}  {case.title}")
    for name in ADMISSION_AGENTS:
        if name not in result.results:
            print(f"  {name:<16} did not run")
            continue
        output = result.results[name].output
        print(f"  {name:<16} {output.decision:<24} confidence {output.confidence:.2f}  next: {output.next}")
        for finding in output.findings:
            print(f"      - {finding.issue}: {finding.evidence}  [{finding.source}]")
    print(f"State: {result.state}")
    if "preauth" in result.results:
        package = result.results["preauth"].data
        print(f"Pre-auth requested: ₹{package.requested_amount:,} (cost model); justification by {package.justification_by}")
        print(f"  {package.justification}")
    for flag in result.review_flags:
        print(f"Needs a person: {flag}")
    for name in result.failed:
        print(f"Escalated: {name} failed twice")


if __name__ == "__main__":
    main()
