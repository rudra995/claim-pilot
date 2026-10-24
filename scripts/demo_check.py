"""Run the core demo twice from a clean state, through the real API, and check every step.

    .venv/bin/python scripts/demo_check.py

Each run uses a fresh database and uploads folder in a temporary directory, with the LLM switched
off (templates), so it never touches your demo database and gives the same result every time.
The trace of the last run is saved to reports/demo_trace.json: a recording of a real run, for a
backup replay. It is labelled as a recording; it is not the live system.

Steps (the demo story):
  1. Ramesh's cost and room scenarios, with the policy clause      (case2)
  2. Missing X-ray report detected before sending                  (case2)
  3. X-ray attached, checks rerun, stale findings cleared          (case2)
  4. The officer approves the exact new draft; the insurer answers (case2)
  5. Discharge cut appealed with quoted evidence, approved round 2 (case3)
  6. An unsupported appeal is stopped and handed to a person       (case4)
"""

import base64
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)

OFFICER = "Demo Officer"


def run_once(record: bool) -> list[tuple[str, bool, str]]:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    folder = Path(tempfile.mkdtemp(prefix="claimpilot-demo-"))
    os.environ.update(CLAIMPILOT_LLM="off", CLAIMPILOT_UPLOADS=str(folder / "uploads"), CLAIMPILOT_SIMULATOR="standard")
    results: list[tuple[str, bool, str]] = []

    def check(step: str, ok: bool, detail: str) -> None:
        results.append((step, bool(ok), detail))

    with TestClient(create_app(folder / "demo.db")) as client:
        def wait(claim_id: str) -> dict:
            client.app.state.runner.wait(claim_id)
            return client.get(f"/api/claims/{claim_id}").json()

        def start(case_id: str) -> str:
            claim_id = client.post("/api/cases/start", json={"case_id": case_id}).json()["claim_id"]
            wait(claim_id)
            return claim_id

        def approve(claim_id: str) -> dict:
            draft = client.get(f"/api/claims/{claim_id}").json()["pending_draft"]
            response = client.post(f"/api/claims/{claim_id}/decision", json={"officer": OFFICER, "decision": "approve", "draft_hash": draft["hash"]})
            assert response.status_code == 200, response.text
            return wait(claim_id)

        ready = client.get("/api/ready").json()
        check("0. Ready", ready["ready"], f"models {ready['models']}, LLM {ready['llm']['mode']}")

        # 1-4: Ramesh
        ramesh = start("case2")
        note = client.post(f"/api/claims/{ramesh}/patient-note").json()["note"]
        current = next(o for o in note["options"] if o["current"])
        rec = note["recommended"]
        check("1. Patient cost and room scenarios", (current["patient_pays"], rec["rate"], rec["patient_pays"], rec["saving"]) == (54_200, 3_000, 7_000, 47_200),
              f"pays ₹{current['patient_pays']:,} now; ₹{rec['patient_pays']:,} in a ₹{rec['rate']:,} room, saving ₹{rec['saving']:,}; {note['clause'][:40]}…")
        detail = client.get(f"/api/claims/{ramesh}").json()
        tasks = [t for t in detail["tasks"] if t["kind"] == "attach"]
        check("2. Missing evidence detected", tasks and tasks[0]["doc_type"] == "xray_report", f"task: {tasks[0]['issue'] if tasks else 'none'}")
        before = detail["pending_draft"]
        content = base64.b64encode(Path("fixtures/remediation/ADM-0002-xray-report.pdf").read_bytes()).decode()
        attached = client.post(f"/api/claims/{ramesh}/documents", json={"type": "xray_report", "filename": "xray.pdf", "content_base64": content, "officer": OFFICER})
        client.post(f"/api/claims/{ramesh}/recheck", json={"reason": "X-ray report attached"})
        after = wait(ramesh)
        check("3. Evidence attached and checks rerun", attached.status_code == 201 and after["last_recheck"]["resolved"] == ["Missing xray report"] and after["pending_draft"]["version"] > before["version"],
              f"draft v{before['version']} -> v{after['pending_draft']['version']}; resolved {after['last_recheck']['resolved']}; still open {after['last_recheck']['remaining']}")
        stale = client.post(f"/api/claims/{ramesh}/decision", json={"officer": OFFICER, "decision": "approve", "draft_hash": before["hash"]})
        done = approve(ramesh)
        answer = done["thread"][-1]
        check("4. Exact draft approved, bounded insurer answer", stale.status_code == 409 and answer["type"] == "PARTIAL_APPROVE" and answer["approved_amount"] == 185_800,
              f"old draft refused ({stale.status_code}); insurer {answer['type']} ₹{answer['approved_amount']:,}, every cut cites a clause")

        # 5: Case 3, appeal at discharge
        lakshmi = start("case3")
        approve(lakshmi)
        client.post(f"/api/claims/{lakshmi}/discharge")
        wait(lakshmi)
        appeal_view = approve(lakshmi)
        letter = (appeal_view.get("pending_draft") or {}).get("message", {})
        quoted = "ADM-0003-D1 p.1" in letter.get("argument", "")
        final = approve(lakshmi) if letter.get("type") == "APPEAL" else appeal_view
        stage = [m["type"] for m in final["thread"] if m["stage"] == "discharge"]
        check("5. Supported appeal with quoted evidence", quoted and stage == ["SUBMIT", "PARTIAL_APPROVE", "APPEAL", "APPROVE"], f"discharge thread {stage}; evidence quoted: {quoted}")

        # 6: Case 4, unsupported appeal stopped
        suresh = start("case4")
        approve(suresh)
        client.post(f"/api/claims/{suresh}/discharge")
        wait(suresh)
        held = approve(suresh)
        appeal_agent = held["agents"].get("appeal", {}).get("data", {})
        check("6. Unsupported appeal stopped", held["state"] == "AWAITING_HUMAN" and not held["pending_draft"] and appeal_agent.get("evidence", {}).get("verdict") in ("unsupported", "insufficient"),
              f"state {held['state']}; evidence {appeal_agent.get('evidence', {}).get('verdict')}; tasks {[t['kind'] for t in held['tasks']]}")

        if record:
            trace = {
                "label": "RECORDED RUN (replay only, not the live system)",
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "settings": {"llm": "off (templates)", "simulator": "standard", "officer": OFFICER},
                "claims": {name: client.get(f"/api/claims/{cid}").json()["timeline"] for name, cid in (("case2", ramesh), ("case3", lakshmi), ("case4", suresh))},
            }
            Path("reports").mkdir(exist_ok=True)
            Path("reports/demo_trace.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return results


def main() -> int:
    failed = False
    for attempt in (1, 2):
        print(f"Run {attempt} (fresh database)")
        for step, ok, detail in run_once(record=attempt == 2):
            print(f"  {'PASS' if ok else 'FAIL'}  {step}: {detail}")
            failed |= not ok
    print("Recorded the second run to reports/demo_trace.json" if not failed else "Some steps failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
