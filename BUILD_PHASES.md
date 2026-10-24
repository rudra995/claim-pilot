# ClaimPilot build phases

Build one phase at a time. Each phase lists what to build, which sections of `docs/design.md` to read, and the checks that must pass before committing.

## How to run a phase

Paste this into Claude Code, changing the phase number:

```
Read CLAUDE.md, then Phase N in BUILD_PHASES.md and the design sections it lists.
Show me your plan first. After I approve, build Phase N only, run every check
listed for it, show me the results, and commit with both co-authors.
```

Then check the results yourself, run the app if the phase has something to look at, and move on. Take turns giving the prompt.

If a phase is too big for one go, ask Claude to split it into parts a and b and commit each.

---

## Phase 0: Contracts and fixtures

**Read:** sections 3, 4, 5, 6, 8

**Build:**
- `backend/schemas/`: `ClaimState` enum, `AgentOutput` and `Finding` (Pydantic), insurer message types (SUBMIT, QUERY, RESPONSE, APPROVE, PARTIAL_APPROVE, DENY, APPEAL, FINAL), `Rule` model with the rule types from section 6
- `backend/db/schema.sql` and a small `db.py` that creates the tables (SQLite is fine)
- `fixtures/`: three hand-written demo cases as JSON (patient, policy, admission, bill lines, documents list):
  - Case 1: clean appendix surgery
  - Case 2: Ramesh's knee replacement: ₹5,000 room on a ₹3,00,000 policy with a 1% room cap, pre-op X-ray report missing
  - Case 3: a claim TPA-B will partly deny (discharge summary doesn't say why admission was needed)

**Checks:**
- [ ] All three fixtures load and validate against the schemas
- [ ] The database creates from scratch with no errors
- [ ] `pytest` runs (even if only schema tests exist)

---

## Phase 1: Rules engine

**Read:** sections 1 (worked example) and 6

**Build:**
- `backend/rules/engine.py`: applies rules to bill lines in this order: non-payables, room cap and proportionate cut, sub-limits, co-pay, sum-insured ceiling
- Returns payable amount, patient share, and a deduction list where every deduction cites its rule and clause
- A `what_if_room(rate)` function for the room advisor later

**Checks:**
- [ ] `tests/test_ramesh.py`: billed ₹2,40,000, insurer pays ₹1,85,800, patient pays ₹54,200
- [ ] Same case with a ₹3,000 room: patient pays ₹7,000
- [ ] Tests for at least one sub-limit, one co-pay and one waiting-period case pass

---

## Phase 2: Synthetic data

**Read:** section 8

**Build:**
- `data/generator/`: 25 procedures with cost ranges; ~1,000 patients, policies and admissions with ~15 bill lines each
- Planted problems at the shares in section 8
- Three TPA strictness profiles (section 5)
- Claim outcomes from the rules engine plus TPA profiles plus a little noise, saved as labels
- A fixed random seed so data is the same on every machine

**Checks:**
- [ ] 1,000 admissions in the database
- [ ] Each planted problem's share is within ±3 points of the target
- [ ] Re-running the generator gives identical data

---

## Phase 3: Documents

**Read:** section 8

**Build:**
- Discharge summaries and lab reports written by the LLM from the structured data, rendered to PDF
- 50 of them as scanned-looking images
- Missing-document cases really have that document missing
- A ground-truth JSON per document (the true field values)

**Checks:**
- [ ] Document count matches admissions minus planted missing documents
- [ ] Open 3 PDFs at random and confirm they match their ground truth

Tip: generate documents for a sample of ~200 admissions first if LLM calls are slow, plus all three demo cases.

---

## Phase 4: Policy ingestion and search

**Read:** section 6

**Needs:** 3–4 real policy PDFs in `data/policies/`

**Build:**
- Split each policy by clause number; store in ChromaDB with clause ids
- LLM extracts rule JSON per policy into `data/rules/`, marked `verified_by_human: false`
- A small script to print extracted rules for a person to check and mark verified

**Checks:**
- [ ] Searching "room rent limit" returns the room rent clause for every policy
- [ ] Every extracted rule validates against the `Rule` schema
- [ ] You've checked the room cap rule of each policy by hand

---

## Phase 5: ML models

**Read:** section 7

**Build:**
- Cost and length-of-stay estimator (XGBoost, 50th and 90th percentile)
- Denial-risk classifier with SHAP; `fixes_map.py` mapping top features to plain fixes
- Time-based train/test split; metrics saved to `models/metrics.json`

**Checks:**
- [ ] 85–95% of test bills fall under the 90th-percentile estimate
- [ ] Denial model ROC-AUC printed and above 0.75
- [ ] For Case 2, top reasons include the missing X-ray report and the room cap

---

## Phase 6: Hospital agents, admission flow

**Read:** sections 3 and 4

**Build:**
- Orchestrator (LangGraph) with the state machine
- Document reader, clinical coder, policy agent, cost estimator (policy and cost in parallel), pre-auth agent
- Every agent returns `AgentOutput`; invalid output retries once, then escalates
- Every step written to `audit_log`

**Checks:**
- [ ] `python -m backend.run_case fixtures/case1.json` takes Case 1 to `AWAITING_HUMAN`
- [ ] Every agent output in the audit log validates and every finding has a source
- [ ] The pre-auth amount comes from the cost model, not the LLM

---

## Phase 7: Denial-risk agent and human gates

**Read:** sections 4 and 9

**Build:**
- Denial-risk agent: model score plus checklist rules, suggested fixes
- Approval gate: approve, edit or reject; nothing moves to `SUBMITTED` without it
- Masking of names, phones and ID numbers before every LLM call

**Checks:**
- [ ] Case 2 is flagged with the missing X-ray report and the room cap, each with a source
- [ ] A claim can't reach `SUBMITTED` without an approval record
- [ ] No unmasked patient name appears in logged prompts

---

## Phase 8: Insurer side and negotiation

**Read:** section 5

**Build:**
- Insurer agents (adjudicator, medical reviewer, fraud screen, query writer) as a separate graph
- Message exchange; hospital and insurer code never import each other
- Query responder and appeal agent on the hospital side
- Three-round limit, then a human decides

**Checks:**
- [ ] Case 1 is approved
- [ ] Case 2 (after fixes) is approved with cited deductions
- [ ] Case 3 is partly denied, appealed, and accepted in round two
- [ ] A claim forced past three rounds lands in the human queue

---

## Phase 9: Rest of the stay

**Read:** sections 4 and 10

**Build:**
- Enhancement agent, discharge agent, reconciliation agent
- Patient agent with the room advisor, using `what_if_room`

**Checks:**
- [ ] Case 2's room advisor says the ₹3,000 room saves about ₹47,200
- [ ] A bill pushed past the approved amount triggers an enhancement request
- [ ] Reconciliation separates valid and recoverable deductions for Case 3

---

## Phase 10: API and live trace

**Read:** sections 11 and 12

**Build:**
- FastAPI endpoints: list claims, claim detail, start a case, approval actions, insurer thread, dashboard numbers
- WebSocket that streams each agent step as it happens

**Checks:**
- [ ] Starting Case 1 through the API streams every step over the WebSocket
- [ ] Approve and reject work through the API

---

## Phase 11: Frontend

**Read:** section 12

**Build:** claims board, claim detail with agent trace, approval queue, insurer console, patient view

**Checks:**
- [ ] All three demo cases run from the UI start to finish
- [ ] Agent steps appear live, with reasoning and sources

---

## Phase 12: Dashboard, numbers and learning loop

**Read:** sections 2, 7 and 10

**Build:**
- Batch run of 50 test claims with and without the denial-risk agent
- Dashboard: first-pass approval rate, turnaround, rupees recovered, top denial reasons, fairness panel
- Learning loop: TPA profile notes learned from denials, shown on screen

**Checks:**
- [ ] Both batch runs finish; the with/without numbers show on the dashboard
- [ ] At least one learned TPA rule appears with the number of denials it came from

---

## Phase 13: Extras (only if time allows)

**Read:** section 10

Pick in this order: PM-JAY mode, FHIR export, Hindi/Kannada patient notes, hospital-side fraud check. One extra per prompt.

---

## Phase 14: Demo hardening

**Read:** sections 13 and 14

**Build:**
- One command to start everything (Docker Compose)
- A reset script that restores the three demo cases to their starting state
- README with setup steps

**Checks:**
- [ ] Fresh clone to running demo with one command
- [ ] Run the full 5-minute demo script twice without errors

---

## Refinement pass

After Phase 10, a refinement pass revisited Phases 4, 6, 7, 8, 9, 12 and 14 without changing the roadmap above. What changed, how it was checked, and what is still open are in [`docs/REFINEMENT.md`](docs/REFINEMENT.md).
