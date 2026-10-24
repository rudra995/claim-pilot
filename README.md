# ClaimPilot

ClaimPilot helps hospital billing teams prevent avoidable claim queries and explain patient costs before submission, with policy-backed calculations and human approval.

It runs a cashless insurance claim from admission to settlement. Agents read the records, price the stay against the policy, flag what's missing, and draft each message. A billing officer approves every message before it goes out. On the other side is a **simulated** insurer/TPA, used as a test environment. It is not a real insurer integration.

All patient data in this repository is synthetic.

- Design: [`docs/design.md`](docs/design.md)
- Build phases: [`BUILD_PHASES.md`](BUILD_PHASES.md)
- Refinement pass (what changed, evidence, limitations): [`docs/REFINEMENT.md`](docs/REFINEMENT.md)
- Demo runbook: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
- Benchmark results: [`reports/BENCHMARK.md`](reports/BENCHMARK.md)
- UI handoff: [`docs/UI_HANDOFF.md`](docs/UI_HANDOFF.md)

## Requirements

- Python 3.11
- Node 20 or 22 with npm
- Optional: Docker with Compose
- Optional: a local [Ollama](https://ollama.com) model or a Gemini API key. Without one, every agent uses its labelled template. The model only ever writes prose, never amounts or decisions.

## Setup and start

```bash
./scripts/setup.sh      # venv, dependencies, trains the models from the seeded synthetic data, npm ci, .env
./scripts/start.sh      # API on :8000, frontend on :5173
```

Open http://127.0.0.1:5173. `GET /api/ready` reports what works right now: database, trained models, policy search, LLM mode (`live` or `fallback`), and any claims interrupted mid-run.

To start everything with Docker instead:

```bash
docker compose up --build    # frontend http://localhost:8080, API http://localhost:8000/api/ready
```

### Manual setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m backend.models.train_denial
.venv/bin/python -m backend.models.train_cost
cp .env.example .env
(cd frontend && npm ci)
.venv/bin/python -m uvicorn backend.api.main:app --port 8000   # API
(cd frontend && npm run dev)                                   # frontend
```

## Demo state

```bash
.venv/bin/python scripts/reset_demo.py --yes   # empty database, no uploads (touches nothing else)
.venv/bin/python scripts/seed_demo.py          # optional: a morning's claims for the dashboard, signed "Demo seed (scripted)"
.venv/bin/python scripts/demo_check.py         # runs the core demo twice from a clean state and checks every step
```

`demo_check.py` uses its own temporary database. It records the second run to `reports/demo_trace.json` as a replay, which is clearly labelled as a recording.

## Checks

```bash
.venv/bin/python -m pytest                       # all tests
.venv/bin/python -m backend.bench.run            # three-mode benchmark -> reports/benchmark.json, BENCHMARK.md
.venv/bin/python -m backend.bench.models         # model vs simple rules, calibration, subgroups -> reports/model_report.json
.venv/bin/python -m backend.bench.challenge      # reader on independent challenge documents
(cd frontend && npm run build)                   # production build
```

Four policy-search tests are skipped until `python -m backend.rag.ingest` has built the local index.

## Settings

See [`.env.example`](.env.example). The main ones:

| Variable | Meaning |
| --- | --- |
| `CLAIMPILOT_DB` | Demo database file (default `claimpilot.db`) |
| `CLAIMPILOT_LLM` | `off` = always templates (deterministic); unset = try Ollama, then Gemini |
| `CLAIMPILOT_LLM_TIMEOUT` | Seconds per model call (default 60); after a failure the client pauses 60 s |
| `CLAIMPILOT_SIMULATOR` | Simulated insurer habits: `standard` or `held_out` |
| `CLAIMPILOT_OFFICER_TOKENS` | `Name=token,...` to require `X-Officer-Token` on decisions; unset = demo mode, names are self-declared |
| `CLAIMPILOT_UPLOADS`, `CLAIMPILOT_IMPORTS` | Where attached records and imported cases are stored |

## When something is unavailable

| Situation | What happens | What to do |
| --- | --- | --- |
| No LLM reachable | Agents use labelled templates; `/api/ready` says `fallback` | Nothing; amounts and decisions never depend on the model |
| Models not trained | `/api/ready` lists the problem | Run the two `train_*` commands |
| An agent fails twice | The claim goes to a person with the reason | Review it in Tasks |
| The API stopped mid-run | `/api/ready` lists interrupted claims | `POST /api/claims/{id}/resume` carries on without resending |
| A scanned page | Its fields stay blank and a person is asked to read it (there is no OCR) | Attach a text copy, or record the fields as a correction |

## Repository map

```
backend/
  agents/hospital/   document reader, coder, policy, estimator, pre-auth, denial risk, appeal, query responder, ...
  agents/insurer/    the simulated TPA (medical reviewer, fraud screen, adjudicator, query writer)
  agents/orchestrator.py, approval.py   the state machine and the human gate
  evidence/          citation and clinical-evidence checks, shared by both sides
  rules/engine.py    every rupee amount
  exchange/          the only channel between hospital and insurer
  remediation.py     attach records, record corrections
  policy_config.py   versioned policy configurations
  adapters/          billing CSV/JSON import
  bench/             benchmark, model report, challenge scorer (never used by the app)
  api/               FastAPI app and dashboard analytics
data/                generator, policy rules and configs, challenge builder
fixtures/            demo cases, preset patients, challenge set, remediation records, sample import
frontend/            React app
scripts/             setup, start, reset, seed, demo check
reports/             measured benchmark and model results
```
