"""Fill the demo database with a morning's worth of claims, so the dashboard has something to show.

    .venv/bin/python scripts/seed_demo.py            (uses CLAIMPILOT_DB, default claimpilot.db)

It drives the demo cases and the preset patients through the real API, the same way the screens do.
Every approval it records is signed "Demo seed (scripted)", so the audit log and the dashboard show
which decisions were scripted rather than made by a person. Claims are left at different points
(some closed, some with the insurer, some waiting for a person) so the pipeline looks like a real
desk. Run scripts/reset_demo.py first for a clean start. Synthetic data only.
"""

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("CLAIMPILOT_LLM", "off")

SEED = "Demo seed (scripted)"


def main() -> int:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
    from backend.api.store import fixture_cases

    db = os.environ.get("CLAIMPILOT_DB", "claimpilot.db")
    with TestClient(create_app(db)) as client:
        def wait(claim_id: str) -> dict:
            client.app.state.runner.wait(claim_id)
            return client.get(f"/api/claims/{claim_id}").json()

        def start(case_id: str) -> str:
            claim_id = client.post("/api/cases/start", json={"case_id": case_id}).json()["claim_id"]
            wait(claim_id)
            return claim_id

        def act(claim_id: str, decision: str, **extra) -> dict:
            detail = client.get(f"/api/claims/{claim_id}").json()
            body = {"officer": SEED, "decision": decision, "draft_hash": (detail["pending_draft"] or {}).get("hash"), **extra}
            client.post(f"/api/claims/{claim_id}/decision", json=body)
            return wait(claim_id)

        def carry(claim_id: str, until: str) -> dict:
            """Approve drafts and move stages on until the claim reaches `until` or needs a real decision.

            "REVIEW" stops at the first draft waiting for approval; "HELD" stops where agents held back
            and only a person can decide (nothing drafted).
            """
            detail = wait(claim_id)
            for _ in range(12):
                state = detail["state"]
                if state == until or (until == "REVIEW" and state == "AWAITING_HUMAN" and detail["pending_draft"]):
                    break
                if until == "HELD" and state == "AWAITING_HUMAN" and not detail["pending_draft"]:
                    break
                if state == "AWAITING_HUMAN" and detail["pending_draft"]:
                    detail = act(claim_id, "approve")
                elif state == "AWAITING_HUMAN" and "accept" in detail["actions"] and until != "HELD":
                    detail = act(claim_id, "accept")
                elif state == "IN_TREATMENT" and until in ("FINAL_APPROVED", "CLOSED", "HELD"):
                    client.post(f"/api/claims/{claim_id}/discharge")
                    detail = wait(claim_id)
                elif state == "FINAL_APPROVED" and until == "CLOSED":
                    client.post(f"/api/claims/{claim_id}/payment", json={"paid": detail["approved"], "reference": f"UTR-{claim_id}"})
                    detail = wait(claim_id)
                else:
                    break
            return detail

        plan = {"case1": "CLOSED", "case3": "CLOSED", "case4": "HELD"}
        for case_id, until in plan.items():
            carry(start(case_id), until)

        ramesh = start("case2")  # the X-ray report is fetched and attached before sending
        content = base64.b64encode(Path("fixtures/remediation/ADM-0002-xray-report.pdf").read_bytes()).decode()
        client.post(f"/api/claims/{ramesh}/documents", json={"type": "xray_report", "filename": "xray.pdf", "content_base64": content, "officer": SEED})
        client.post(f"/api/claims/{ramesh}/recheck", json={"reason": "X-ray report attached"})
        carry(ramesh, "FINAL_APPROVED")

        presets = [c for c in fixture_cases() if c.startswith("p0")]
        targets = ["CLOSED", "IN_TREATMENT", "FINAL_APPROVED", "CLOSED", "REVIEW", "IN_TREATMENT", "CLOSED"]
        for i, case_id in enumerate(presets):
            carry(start(case_id), targets[i % len(targets)])

        analytics = client.get("/api/analytics").json()
    print(f"Seeded {analytics['claims']} claims into {db}: " + ", ".join(f"{p['label'].lower()} {p['value']}" for p in analytics["pipeline"] if p["value"]))
    print(f"All {analytics['oversight']['scripted_decisions']} decisions are signed '{SEED}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
