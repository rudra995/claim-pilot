# ClaimPilot

Multi-agent system that runs a hospital's cashless insurance claims end to end (pre-auth → enhancement → discharge → settlement → reconciliation), with a simulated insurer/TPA on the other side and a human approving every submission. Built for the AgentOps 2026 hackathon (MIT Bengaluru x CGI).

Full design: `docs/design.md`. Before starting a task, read the section it touches (e.g. section 4 for agents, section 6 for the rules engine). Don't load the whole file unless the task needs it.

## Hard rules (never break these)

- The LLM never does arithmetic. Every rupee amount comes from `backend/rules/engine.py`.
- Every agent returns the shared `AgentOutput` schema in `backend/schemas/`. A finding without a `source` is invalid.
- Claim state changes only through the orchestrator's state machine. Never set `claim.state` anywhere else.
- Hospital agents and insurer agents never import each other. They talk only through `backend/exchange/messages.py`.
- Nothing is sent to the insurer or the patient without passing a human approval gate.
- Synthetic data only. No real patient data anywhere in the repo.

## Shared contracts (change only after both teammates agree)

- `backend/schemas/` — AgentOutput, ClaimState enum, message types, Rule JSON
- `backend/db/schema.sql`

If a task seems to need a change here, stop and tell the user instead of editing.

## How we build

We build in phases from `BUILD_PHASES.md`, one phase per prompt.

- Build only the phase you are asked for. Don't start the next one.
- Before writing code, read that phase in `BUILD_PHASES.md` and the design sections it lists.
- When done, run every check listed under the phase and show the results. If a check fails, fix it before committing.
- Then commit (see Commits) and stop.

## Commits

We work on `main` only: no feature branches, no PRs. Commit straight to `main` and push.

One commit per phase, message `Phase N: <short summary>`. Every commit ends with both teammates as co-authors, after a blank line:

```
Co-authored-by: Rudra Solanki <solankirudra69@icloud.com>
Co-authored-by: Janhavi Bhattad <janhavi.n.bhattad@gmail.com>
```

These two are the only co-authors. Never add Claude (or any AI tool) as a co-author or contributor, and don't add "Generated with" lines.

## Commands

<!-- Update these to match the repo. -->
- Tests: `pytest`
- Backend: `uvicorn backend.api.main:app --reload`
- Frontend: `cd frontend && npm run dev`
- Everything: `docker compose up`

## Conventions

- Python 3.11, type hints everywhere, Pydantic v2.
- Everything happens on `main`. Small commits.
- Any rules-engine change needs a test. `tests/test_ramesh.py` must keep passing (insurer pays ₹1,85,800).
- Prefer simple, readable code over clever code; this is demoed live.

## Domain words

- TPA: middleman that processes claims for the insurer.
- Pre-auth: approval requested at admission. Enhancement: request for more when the bill grows. Final approval: at discharge.
- Proportionate deduction: if the room costs more than the policy's cap, linked charges (doctor fees, nursing, OT) are paid in the same ratio. Implants and medicines usually aren't cut.
- Non-payables: items insurers never pay (gloves, kits). Sub-limit: cap on one procedure. Co-pay: fixed share the patient always pays.
