# Demo runbook

Five minutes, live. Every number on screen comes from the rules engine; the insurer is a **simulation**.

## Before you go on

```bash
.venv/bin/python scripts/demo_check.py           # must print PASS for all 7 steps, twice
.venv/bin/python scripts/reset_demo.py --yes     # clean database, no uploads
.venv/bin/python scripts/seed_demo.py            # optional: a busy-looking dashboard (decisions signed "Demo seed (scripted)")
./scripts/start.sh                               # or: docker compose up
```

Then check `http://127.0.0.1:8000/api/ready`: `ready: true`, `llm.mode` either `live` or `fallback`. Both work. In fallback mode, agents' prose comes from labelled templates.

## The story

1. **Patient cost and the policy clause** (Ramesh, case 2). Open the patient's *Patient estimate* tab. He pays ₹54,200 in the ₹5,000 room, and would pay ₹7,000 in the ₹3,000 room, a ₹47,200 difference. Show clause 4.2 and the assumptions: these are tariff scenarios, not live beds, and a move doesn't reprice past days.
2. **Missing evidence detected.** On the overview, the risk checker flags the missing pre-op X-ray report, with the fix.
3. **Evidence attached and checks rerun.** Attach `fixtures/remediation/ADM-0002-xray-report.pdf` as an X-ray report, then *Attach and recheck*. The journey shows "draft v2 replaces v1, resolved: Missing xray report, still open: room over cap, non-payables".
4. **A person reviews the exact draft.** Note "Approving draft v2 · hash". Send it. The insurer part-approves ₹1,85,800; every cut cites a clause.
5. **A supported appeal** (Lakshmi, case 3). Approve the pre-auth, discharge, send the final claim. The TPA cuts the stay because the summary doesn't say why admission was needed. The appeal quotes "BP 92/60, pulse 112" and the labs, with page links. Approve it, and the insurer approves in round 2.
6. **An unsupported appeal is stopped** (Suresh, case 4). Same path; the records show nothing that needed a bed. The agents don't appeal; the evidence check says *unsupported*, and a person decides.
7. **Honest numbers.** On the dashboard, scroll to "What each part adds". Say it plainly: the checklist removes the queries; the AI layer adds evidence-checked appeals; it's synthetic and simulated.

## If something breaks

| Problem | Do this |
| --- | --- |
| A claim is stuck "working" | Refresh. If `/api/ready` lists it as interrupted, `POST /api/claims/{id}/resume` |
| The LLM is slow or down | Set `CLAIMPILOT_LLM=off` and restart. Nothing numeric changes |
| The database is in a strange state | `scripts/reset_demo.py --yes`, restart, start again |
| The live run fails entirely | Show `reports/demo_trace.json`. Say "this is a recording of a real run", never present it as live |
| A question about real insurers | "The insurer here is simulated; we don't claim real-world approval rates." |
