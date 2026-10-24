# Refinement pass (October 2026)

This pass refines the existing build; it doesn't replace it. The original roadmap in `BUILD_PHASES.md` is unchanged. Each commit names the phase it refines:

| Commit | Scope |
| --- | --- |
| `Phase 8 refinement: evidence-checked appeals and insurer review` | Appeals and insurer decisions rest on evidence |
| `Phase 6 refinement: stage-aware documents and a reader for unfamiliar layouts` | No future information at pre-auth; robust, abstaining reader; challenge set |
| `Phase 7 refinement: fix-and-recheck workflow and draft-bound approvals` | Attach, correct, recheck; approvals bound to draft hashes |
| `Phase 9 refinement: enforced governance, policy versions and bounded failures` | Money only from the engine, policy versions, LLM failure handling, resume |
| `Phase 4 refinement: policy configurations, simulator settings and billing import` | Reusability |
| `Phase 12 refinement: three-mode benchmark and an honest model report` | Business value, measured |
| `Phase 14 refinement: room advisor clarity, demo scripts and Docker Compose` | MVP readiness |
| `Phase 12: dashboard analytics and graphs` | Dashboard charts (plus late fixes found while checking the UI) |

Positioning: *ClaimPilot helps hospital billing teams prevent avoidable claim queries and explain patient costs before submission, with policy-backed calculations and human approval.*

## 1. Starting state

- `origin/main` at `4048a4c`. Once Python 3.11 had its dependencies installed, the existing suite ran clean: **204 passed, 4 skipped**. The skips are policy-search tests that need a local index. The earlier "tests can't be collected" finding came from the environment, not the code. One real reproducibility bug: `pip install -e .` failed on a fresh clone (flat-layout package discovery). This is now fixed in `pyproject.toml`.
- Final state: **266 passed, 4 skipped** (same skips). The frontend production build passes. `scripts/demo_check.py` passes all 7 steps (a readiness check plus the 6-step story), twice, from clean databases. `docker compose` images built and the stack was checked end to end (see section 8 for how it was verified).

## 2. Review findings: what was verified and done

| | Finding | Verified? | What changed |
| --- | --- | --- | --- |
| A | `necessity_argued` became true just because an appeal cited an existing clinical document | **Yes.** `graph.py` set it from document presence and type alone | Replaced by `backend/evidence/clinical.py`. It checks citations (in the claim, same admission, sent, readable) and reads the text for findings. Only a `supported` verdict lifts the cut (§3) |
| B | Extraction tied to a generated "Details" block; scans fall back to people | **Yes** | The fast path is kept. Added other labels, headings and narrative sentences, conflict detection, a basis and text span per field, instruction flagging, and a clear scan task. No OCR (§4) |
| C | Coding by word overlap against the generator's list | **Yes** | Still overlap-based (no larger catalogue, on purpose), now conservative: negated parts dropped, uncertain diagnoses not coded, a 0.6 match threshold, and ties and weak matches abstain |
| D | Very strong model scores on synthetic data | **Yes**: ROC-AUC 0.986 for "not fully approved" | `reports/model_report.json`. A one-line rule scores 0.964. The labels are generated from the same flags, so the score shows the pipeline works, not real-world skill. Calibration and subgroups are reported (§6) |
| E | First-pass metric included partial approvals | **Yes** (`APPROVE` or `PARTIAL_APPROVE`) | Now in full only; part-approvals are reported separately. The frontend scaling was left as it was: its ×100 and ÷100 cancel, as the earlier review said |
| F | Money arithmetic outside `engine.py` | **Yes**: appeal, reconcile, adjudicator, patient advisor, API, frontend | Moved to engine helpers (`total`, `by_clause`, `excess`, `payable_up_to`, `reconcile_payment`, `room_scenarios`, `portfolio`). The API now serves totals, so the browser adds up no rupees. Enforced by tests that scan the source |
| G | Tests couldn't be collected | Environment issue, not code (see §1) | Editable install fixed; `scripts/setup.sh` pins the path |
| H | Which planned features exist? | Before → after: missing-document remediation **no → yes**; evidence navigation **no → yes** (document file endpoint, page links); controlled baseline evaluation **no → yes**; persistent recovery **no → partial** (resume from the audit log and exchange); reset/startup scripts **no → yes**; configuration-driven policies **partial → yes**; learning-loop review controls **no → deferred** | See below |

Two problems surfaced during the work and were fixed:

- **Future information at pre-auth.** The pre-auth read and sent the discharge summary and final bill, which can't exist at admission. Case 3's dispute now happens at discharge, where it belongs.
- **Generator ground truth at runtime.** The denial-risk check read whether the discharge summary states a reason from `Document.fields`, which is the generator's ground truth. It now uses what the document reader actually read.

## 3. Evidence-based appeals

Implemented in `backend/evidence/` (outside the protected schema files) and used by both the hospital appeal agent and the simulated insurer's medical reviewer:

- **Citation checks (deterministic):** each cited document must exist in this claim, belong to this admission, have been sent to the reader, and have readable text. Otherwise it is rejected as `not_in_claim`, `other_admission`, `not_sent` or `unreadable`.
- **Content checks (deterministic):** the records must contain findings that justify inpatient care. These are abnormal vitals or labs, imaging findings, failed outpatient treatment, inability to keep fluids down, or a need for IV treatment or surgery. A diagnosis or procedure line on its own counts for nothing. Negated statements before or after a finding don't count. Statements against admission ("could be managed as an outpatient", "no dehydration") make the evidence `contradictory`.
- **Verdicts:** `supported` (at least one objective finding and two findings in all), `insufficient`, `unsupported`, `contradictory`. Only `supported` is argued or accepted. Everything else becomes a person's task. Nothing is manufactured.
- **Grounded output:** every argued point quotes its passage with document and page. An optional LLM pass (`CLAIMPILOT_EVIDENCE_LLM=1`) can add quotes, but only if they appear word for word in the document. It can never make evidence `supported` on its own, and it never sees the patient block.
- **Policy versus necessity disputes:** cuts the policy wording supports are accepted, never appealed. A policy dispute is argued from the clause text. An unknown clause goes to a person.
- **Bounds:** the 3-round limit and human gate are unchanged. An appeal the insurer has already refused is not sent again. Nothing guarantees success after any number of rounds.

The seven required tests are in `tests/test_evidence.py` and `tests/test_remediation.py`:

1. Relevant evidence supports an appeal.
2. Irrelevant but existing records are not argued, and the insurer isn't persuaded by them.
3. Contradictory records escalate.
4. Missing, foreign and unsent citations are rejected.
5. A valid policy deduction is accepted.
6. The round limit holds.
7. Revised content needs a fresh approval.

## 4. Document understanding

- The reader takes the fast path, then other labels and headings, then narrative, keeping page and text for each field.
- `confidence` is documented as a fixed tier for how a value was found (`basis`), **not** a calibrated probability.
- Conflicting values are kept out of use until a person picks one. Missing critical fields are reported, never guessed. Instruction-like text is flagged and ignored.
- Pre-auth reads only admission-time records (`backend/stages.py`); both sides use the same list.
- The challenge set (`fixtures/challenge/`, built by `data/challenge/build.py`, which never imports the generator) has 10 documents:
  - narrative prose
  - upper-case headings
  - negations
  - a missing admission justification
  - two different ICD-10 codes in one document
  - an unrelated dental report
  - a document telling "the AI" to approve
  - a scan
  - a two-page report
- Runtime inputs and annotations are separate files. This set was used while building the reader, so it is a regression set (49/49).
- A separate **held-out** set of 6 documents was written after the reader was frozen and never tuned on: **26/27**. The miss is the coder abstaining on "Community acquired pneumonia", a safe failure. It was re-scored once after a later fix that split X-ray findings from impressions, with the same result.

## 5. Fix and recheck, and approval integrity

- **Attach** a synthetic PDF (PDF only, 5 MB and 20 pages at most, stored by content hash; duplicates recognised) or **record a correction** (who, why; the original is untouched). Then **recheck**.
- Recheck re-reads the documents, reruns the dependent agents, supersedes the old draft with a new version, and logs what was resolved, what is still open, and what is new. It works for a pre-auth, a query response, a final package, and a cut the agents held back from appealing.
- Drafts carry a version and content hash. An approval is bound to that hash and refused if the draft has changed. A repeated decision (double click, retry) changes nothing. Records sent with a response or appeal travel on the exchange's attachment channel.
- Identity: optional officer tokens (`CLAIMPILOT_OFFICER_TOKENS`). Without them, decisions are marked `self-declared`. **This is not production authentication.**
- Acceptance demo, checked by `scripts/demo_check.py` and tests: missing X-ray → attach → recheck → updated findings → approval of draft v2 (v1's approval is refused) → submission → ₹1,85,800 with cited deductions.

## 6. Measured results (synthetic, simulated insurer)

From `reports/BENCHMARK.md`, regenerated with `python -m backend.bench.run`. The run covers 55 eval cases, from the model's time-based test split, up to 8 per planted problem. Every mode had the same records and the same approval gate. The LLM was off (templates).

| Eval cases, standard insurer | basic | rules | claimpilot |
| --- | --- | --- | --- |
| Pre-auth approved in full, first answer | 27% | 29% | 29% |
| Pre-auth first answer without a query | 98% | 100% | 100% |
| Insurer queries (all stages) | 5 | 0 | 0 |
| Planted issues found before sending (of 47) | 0 | 47 | 47 |
| Flags with no planted issue | 0 | 0 | 0 |
| Appeals sent / wrong | 0 / 0 | 2 / 0 | 2 / 0 |

Under the **held-out insurer**, ClaimPilot sent one evidence-backed necessity appeal and recovered ₹20,690 (simulated). The other modes recovered nothing. In the **appeal challenge**, where the records were rewritten so the cut is right, no mode appealed. ClaimPilot held back both cuts and handed them to a person.

What this honestly says:

- The first-pass and query gains come from the **deterministic checklist**, which rules and claimpilot share. The AI layer doesn't add to them.
- What the claimpilot mode adds is **evidence-checked necessity appeals**. They are argued when the records support them and held back when they don't. One eligible necessity cut (SYN-ADM-0045) was held back because its records showed no findings. That is a conservative miss.
- The simulated insurer judges appeals with the same evidence check, so recovery is a consistency check, not a measured win rate. The simulated TPA-C never concedes a policy appeal, so policy appeals recovered nothing.
- Package preparation takes about 0.09 s (median agent time). That is not insurer turnaround and not staff time. Staff minutes appear only as labelled assumptions: 10 minutes per record fetched and 3 per approval gate.

Model report (`reports/model_report.json`, 200 time-split test claims):

| Not fully approved | ROC-AUC | Recall | Precision | Brier |
| --- | --- | --- | --- | --- |
| Denial model | 0.986 | 0.953 | 1.000 | 0.021 |
| Rule: any flag raised | 0.964 | 0.981 | 0.954 | 0.035 |

Subgroups (age band, gender; each n ≥ 63): false-negative rates of 2–8% and no false positives. Gender is not a feature, but that alone does not make the model fair. These are synthetic labels, so none of this is a fairness claim.

## 7. The five criteria

| Criterion | Evidence in the repository |
| --- | --- |
| **Reusability** | Versioned policy configurations run through the same workflow with no code change: Ramesh pays out ₹1,85,800 under one and ₹1,57,500 under the other (`tests/test_reusability.py`). LLM-extracted real-policy rules load as configurations and stay unverified. The simulator switches to held-out habits by setting. A billing CSV/JSON import gives row-level errors and is idempotent. `backend/evidence` is shared by both sides without either importing the other |
| **Governance and responsibility** | Human gate with hash-bound approvals and idempotent decisions. Evidence-checked appeals that never manufacture justification. Untrusted document text is flagged and never obeyed, and LLM replies carrying orders are discarded. Money only from the engine, enforced by tests. Policy version fingerprints and reproducible calculation records. Unverified and conflicting rules go to a person. Bounded LLM failures with labelled fallback. Masking at every LLM boundary. Append-only audit log |
| **ROI / business value** | Reproducible three-mode benchmark with fair baselines, labels kept out of the hospital side, a held-out insurer, an appeal challenge, and per-case JSON. Observed numbers are kept apart from assumptions. The dashboard shows live metrics and the benchmark, labelled as synthetic |
| **MVP readiness** | Setup, start and reset scripts. `.env.example`. Docker Compose. `/api/ready` separates API, models and LLM mode. Resume after interruption. A demo check that runs twice from clean state. A recorded trace labelled as a replay |
| **Originality** | Room advisor with engine-priced scenarios and clause citation. Evidence verdicts that hold back weak appeals. Fix-and-recheck with versioned drafts. Stage-aware documents. An honest benchmark showing which layer adds what |

## 8. Limitations

**Implemented and verified (tests or recorded runs):**

- Evidence checks: citations, findings, negation, contradiction
- Hash-bound approvals and idempotent decisions
- Fix and recheck, including held-back appeals
- Stage-aware documents
- Policy configurations, held-out simulator and billing import
- Engine-only money, with source-scanning tests
- LLM cooldown and off switch
- Readiness and resume
- Benchmark, model report and challenge scoring
- Analytics endpoint and dashboard charts (rendered and screenshotted at desktop and phone width)
- Demo check passing twice
- Docker images built and the stack exercised (in this sandbox the images were built with a temporary proxy and CA shim for package downloads; the committed Dockerfiles have no such shim)

**Implemented but unverified:**

- A live LLM: none was reachable here. Every run used templates, so the LLM-written justification and appeal paragraphs, and the optional evidence LLM pass, were exercised only with fakes in tests.
- `scripts/setup.sh` and `start.sh` were not run end to end on a fresh machine. Their steps were each run by hand.
- Officer tokens are tested, but they are a demo boundary, not authentication.

**Simulated:**

- The insurer/TPA, its habits, and every insurer decision, query and "recovery". It uses the same evidence checker as the hospital.
- Patients, policies (two hand-written configurations), documents, outcomes and labels, all synthetic.
- The benchmark reviewer, which approves drafts unchanged. It exists only in `backend/bench`; the app never approves without a person.

**Deferred:**

- OCR or vision for scans. Scans become a person's task.
- A larger code catalogue.
- FHIR/NHCX export (not implemented, so no conformance is claimed) and PM-JAY mode.
- The learning loop: insurer patterns as reviewable hypotheses.
- Real authentication and roles.
- Durable job queue and tamper-proof audit. The audit log is append-only by database trigger, not cryptographically sealed.
- Moving the procedure catalogue, room tariff and document checklist out of `data/generator` into a domain module. The hospital side imports them as configuration, never the TPA profiles or labels, which is enforced by a test.

## 9. Shared-contract changes that need both teammates' agreement

None were made. `backend/schemas/` and `backend/db/schema.sql` are untouched. Proposed, smallest first:

1. **`Appeal.evidence`**: an optional list of `{doc_id, page, passage, assertion}`. Today the passages travel inside the argument text, and the insurer re-derives them.
2. **Separate ground truth from the runtime document.** `Document.fields` is both the generator's ground truth and part of the runtime case file. Runtime code no longer reads it, but a `truth` file per document (already written by the generator) would remove the temptation.
3. **`Admission.tpa_id`** is a closed `Literal["TPA-A","TPA-B","TPA-C"]`. A real hospital would need any payer id, so this should become a string checked against configuration.
4. Optionally, `drafts` and `approvals` tables. They are derived from the audit log today, which works but is slower to query.
