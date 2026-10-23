# ClaimPilot: Multi-Agent Hospital Insurance Claims System

Oct 23, 2026 · @Jaan and Rudra

## 1. How cashless hospital insurance works

A cashless claim is approved in three rounds (admission, mid-stay, discharge), and every round can be cut by rules buried in a 40-page policy. That buried-rules problem is what ClaimPilot solves.

### Words you need

| Term | Plain meaning |
| --- | --- |
| Sum insured | The most the policy pays in a year |
| TPA (Third Party Administrator) | The middleman that processes claims for the insurer |
| Cashless | The insurer pays the hospital directly; the patient pays only what isn't covered |
| Pre-authorisation | Approval asked for at admission, with an estimated cost |
| Enhancement | A request for more money when the bill outgrows the approval |
| Final approval | Approval of the final bill; the patient can't leave until it arrives |
| Deduction | The part of the bill the insurer refuses to pay |
| Non-payables | Items insurers never pay for (gloves, masks, admission kits) |
| Sub-limit | A cap on one treatment, such as a fixed amount for cataract surgery |
| Waiting period | Conditions not covered in the first years of a policy |
| Co-pay | A fixed share of every claim the patient always pays |
| ICD-10 | Standard codes for diagnoses that claims must use |

### The journey of one claim

1. **Admission:** the hospital sends a pre-authorisation request with the diagnosis, planned treatment and estimated cost.
2. **Insurer review:** the TPA approves an amount, asks a question (a query), or denies.
3. **During the stay:** if the bill grows past the approved amount, the hospital sends an enhancement request.
4. **Discharge:** the hospital sends the final bill and discharge summary. The TPA approves the final amount, often after hours.
5. **Settlement:** the insurer pays the hospital, usually less than billed.
6. **Reconciliation:** the hospital checks what was paid against what was billed and chases gaps that weren't valid.

### Worked example: Ramesh's knee replacement

Ramesh has a ₹3,00,000 sum insured. His policy caps room rent at 1% of that, so ₹3,000 a day. He picks a ₹5,000 room for 4 days.

Many Indian policies then apply a **proportionate deduction**: charges linked to the room (doctor fees, nursing, operation theatre) are paid only in the same ratio as the room rent.

```latex
\text{ratio} = \frac{\text{room-rent cap}}{\text{actual room rent}} = \frac{3000}{5000} = 0.6
```

| Bill item | Billed (₹) | Insurer pays (₹) | Why |
| --- | --- | --- | --- |
| Room, 4 days × ₹5,000 | 20,000 | 12,000 | Capped at ₹3,000 a day |
| Surgeon and doctor fees | 60,000 | 36,000 | Cut to 60% |
| Nursing | 8,000 | 4,800 | Cut to 60% |
| Operation theatre | 30,000 | 18,000 | Cut to 60% |
| Knee implant | 90,000 | 90,000 | Not linked to the room |
| Medicines | 25,000 | 25,000 | Not linked to the room |
| Consumables (gloves, kits) | 7,000 | 0 | Non-payable |
| **Total** | **2,40,000** | **1,85,800** |  |

Ramesh pays ₹54,200. In a ₹3,000 room he would have paid only ₹7,000 (the consumables). A room ₹8,000 dearer cost him ₹47,200 more.

Which charges get cut, and by how much, differs between policies and is written in each policy's wording. That is why ClaimPilot reads the policy instead of hardcoding rules.

## 2. The problem and the pitch

Today, hospitals find out a claim is wrong when the insurer rejects it. ClaimPilot checks every claim before it is sent, so it goes through the first time.

| Who | Pain today | What ClaimPilot changes |
| --- | --- | --- |
| Patient | Waits hours at discharge; learns the bill at the end | Gets an out-of-pocket estimate and room advice at admission |
| Hospital billing desk | Reads long policies by hand, fills forms, chases the TPA | Agents assemble, check and draft; staff approve |
| Hospital finance | Deductions and denials leak revenue | Denial-risk check, appeals and reconciliation recover it |
| Insurer / TPA | Incomplete claims cause rounds of queries | Receives complete, cited submissions |

**One-line pitch:** ClaimPilot is a team of AI agents that runs a hospital's insurance claims from admission to final payment, and catches rejections before the insurer does, with a human approving every submission.

**Numbers to show on stage** (measured on your test set, and say so):

- First-pass approval rate, with and without the denial-risk agent
- Time from discharge request to a ready final-approval package
- Error of the out-of-pocket estimate against the final settlement
- Rupees recovered through appeals and reconciliation
- Share of agent decisions that needed a human

**Why CGI will care:** one process spans three of their client types (hospitals, insurers, government schemes), and it is the kind of systems integration plus AI they sell.

## 3. System architecture

Two teams of agents, one for the hospital and one for the insurer, exchange each claim through a shared message log, while an orchestrator moves the claim through fixed states.

**The parts:**

- **Orchestrator:** a LangGraph supervisor that reads a claim's state and calls the next agent. Code decides the stage; the LLM never does. That keeps the system predictable and auditable.
- **Hospital agents:** twelve agents, each with one job (section 4).
- **Insurer agents:** a simulated TPA that reviews, queries and denies (section 5).
- **Tools:** the policy rules engine, search over policy documents, the ML models, the document reader and the database.
- **Claim exchange:** an append-only message log that both sides write to. It mimics a real claims exchange: the two sides share no memory and only talk through messages.
- **Human gates:** an approval queue in the UI. Nothing is sent to the insurer or the patient without a person approving it.
- **Audit log:** every agent and human action, with inputs, outputs and time.

The policy agent and the cost estimator run in parallel, since neither needs the other's output.

&#91;embedded content: claim approval cycle · hospital and insurer agents\]

Every approval round runs this loop: a claim leaves the hospital only after the risk check and a person sign off, and any query or denial comes back through the same check.

### Claim states

The orchestrator stores one of these states per claim. Use them as your enum in code.

| State | Who acts next |
| --- | --- |
| ADMITTED | Document reader, clinical coder |
| PREAUTH\_DRAFT | Policy agent and cost estimator, then pre-auth agent |
| RISK\_CHECK | Denial-risk agent |
| AWAITING\_HUMAN | Billing officer (approve, edit, reject) |
| SUBMITTED | Insurer agents |
| QUERY\_RAISED | Query responder, then back to RISK\_CHECK |
| APPROVED / PARTIALLY\_APPROVED | Next stage of the stay |
| DENIED | Appeal agent, then RISK\_CHECK |
| IN\_TREATMENT | Enhancement agent watches the bill |
| DISCHARGE\_DRAFT | Discharge agent |
| FINAL\_APPROVED | Settlement |
| SETTLED | Reconciliation agent |
| CLOSED | Nobody; claim archived |

## 4. Agent specifications

The orchestrator plus twelve hospital-side agents, each with one job, a structured output, and a rule for when it must stop and ask a human.

### The shared output format

Every agent returns the same shape, validated with Pydantic. A finding without a source is rejected and the agent retries once, then escalates.

```json
{
  "agent": "denial_risk",
  "claim_id": "CLM-0042",
  "decision": "fix_before_submit",
  "confidence": 0.81,
  "findings": [
    {
      "issue": "Room rent above policy cap",
      "evidence": "Bill line 3: ₹5,000 per day",
      "source": "Policy clause 4.2"
    }
  ],
  "next": "human_approval"
}
```

### The agents

| Agent | Reads | Tools | Produces | Asks a human when |
| --- | --- | --- | --- | --- |
| Orchestrator | Claim state, last output | State machine | The next step | An agent fails twice |
| Document reader | PDFs and scans | Vision LLM, OCR | Fields with page references | A field's confidence is below 0.8 |
| Clinical coder | Diagnosis and procedure text | ICD-10 lookup, LLM | Codes with reasoning | Two codes fit equally |
| Policy agent | Policy, patient history | Rules engine, policy search | Limits, exclusions, waiting periods, each with its clause | A clause is ambiguous |
| Cost estimator | Case details | Cost model | Typical and high bill, length of stay | The case is unlike its training data |
| Pre-auth agent | Outputs of the four above | Form template | Pre-auth package with justification | Always (gate) |
| Denial-risk agent | Any package before sending | Denial model, checklist rules | Risk score, reasons, suggested fixes | Risk is above the threshold |
| Enhancement agent | Running bill | Bill feed, cost model | Enhancement request with reasons | Always (gate) |
| Discharge agent | Discharge summary, final bill | Rules engine | Final package, deduction preview | Always (gate) |
| Query responder | Insurer's query | Document store | Reply with the requested evidence | A requested document doesn't exist |
| Appeal agent | Denial, records, policy | Policy search, rules engine | Appeal letter citing clauses and records | Always (gate) |
| Reconciliation agent | Payment advice, bill | Rules engine | Gap report: valid vs recoverable deductions | A gap is above a set amount |
| Patient agent | Estimate, deduction preview | Rules engine, translation | Plain-language cost note, room advice | The note mentions a denial |

### Design rules

- The LLM never does arithmetic. Every rupee comes from the rules engine.
- Each agent sees only what it needs, which keeps prompts short and failures easy to trace.
- Agents can't send anything outside the hospital. Only an approved gate does.

## 5. The insurer side (simulated TPA)

A simulated TPA with its own agents reviews every claim, raises queries and denials, and negotiates for up to three rounds per stage before a human steps in.

| Insurer agent | Job |
| --- | --- |
| Adjudicator | Applies the policy rules and its own strictness profile; sets the approved amount |
| Medical reviewer | Checks the treatment is justified by the records (was surgery urgent, does length of stay fit) |
| Fraud screen | Flags upcoding, unusually long stays and duplicate claims |
| Query writer | Turns gaps into specific requests ("send the pre-op X-ray report") |

### Strictness profiles: why the insurer isn't rigged

Each simulated TPA has a hidden profile, for example:

- **TPA-A** always asks for the X-ray report on orthopaedic claims.
- **TPA-B** denies if the discharge summary doesn't state why admission was needed.
- **TPA-C** applies proportionate deduction strictly, including to pharmacy.

Hospital agents never see these profiles. They learn them from past queries and denials (the learning loop in section 10). So outcomes come from real gaps in the claim, not a script, and you can say so if judges ask.

### Message protocol

| Type | From | Contains |
| --- | --- | --- |
| SUBMIT | Hospital | Package, documents, requested amount |
| QUERY | Insurer | What is missing and why |
| RESPONSE | Hospital | The answer and attached evidence |
| APPROVE / PARTIAL\_APPROVE | Insurer | Approved amount, each deduction with its clause |
| DENY | Insurer | Reason and clause |
| APPEAL | Hospital | Counter-argument citing clauses and records |
| FINAL | Insurer | Final decision for this stage |

```json
{
  "type": "PARTIAL_APPROVE",
  "claim_id": "CLM-0042",
  "round": 1,
  "approved_amount": 185800,
  "deductions": [
    {"item": "Consumables", "amount": 7000, "clause": "Annexure II, non-payable items"},
    {"item": "Room rent above cap", "amount": 8000, "clause": "4.2 room rent limit"},
    {"item": "Linked charges", "amount": 39200, "clause": "4.2 proportionate deduction"}
  ]
}
```

**Rules both sides follow:** at most three rounds per stage, then a human decides; every deduction cites a clause; the appeal agent may only cite documents that exist in the claim.

## 6. Policy rules engine and policy search

The LLM reads each policy once and turns it into structured rules; plain Python applies those rules, so every rupee of deduction is calculated, never guessed.

### How it works

1. **Split by clause:** cut the policy PDF at its clause numbers (4.1, 4.2…), not into fixed-size chunks. Keep the section and clause id as metadata.
2. **Extract rules:** an LLM turns each limit clause into a rule in JSON. A person checks the rules once per policy, in the UI.
3. **Apply rules:** a Python calculator runs the rules over the bill, line by line.
4. **Answer open questions:** search over the clauses (RAG, with ChromaDB) answers questions a rule can't, like "is this a pre-existing condition?", and returns the clause text as evidence.

### Rule types

The example values are illustrative; real ones come from each policy.

| Rule | Example | Applies to |
| --- | --- | --- |
| room\_rent\_cap | 1% of sum insured per day | Room line, plus proportionate cut on linked charges |
| icu\_cap | 2% of sum insured per day | ICU line |
| sub\_limit | Cataract: fixed cap per eye | That procedure's total |
| waiting\_period | Joint replacement: 24 months | Whole claim |
| co\_pay | 10% for patients over 60 | Payable total |
| non\_payable | Gloves, admission kit | Listed items |
| exclusion | Cosmetic surgery | Whole claim |

```json
{
  "rule_id": "POL-A-4.2",
  "type": "room_rent_cap",
  "value": {"percent_of_sum_insured": 1.0, "per": "day"},
  "proportionate": true,
  "linked_charges": ["doctor_fees", "nursing", "ot_charges"],
  "excluded_from_proportionate": ["implants", "pharmacy"],
  "clause_text": "…",
  "verified_by_human": true
}
```

**Order of application:** remove non-payables, then apply room caps and proportionate cuts, then sub-limits, then co-pay, then the sum-insured ceiling. Unit-test the engine on Ramesh's case: it must return ₹1,85,800.

**Why this matters on stage:** judges will ask what happens if the LLM invents a number. It can't, because numbers only come from code, and each rule links to its clause.

## 7. ML models

Two models carry the data work: a cost estimator that sets how much to ask for at admission, and a denial-risk model that flags claims before they are sent.

### Cost and length-of-stay estimator

- **Predicts:** total bill and number of days, as a range: typical (50th percentile) and high (90th percentile).
- **Why a range:** asking too little forces enhancement requests later; asking too much invites scrutiny. The range lets the pre-auth agent justify its number.
- **Features:** procedure, age, number of other conditions, room category, hospital tier, city, emergency or planned.
- **Model:** XGBoost with quantile loss, one model per percentile.

### Denial-risk model

- **Predicts:** approved, partially approved, or denied.
- **Features:** claim details, document-completeness flags (is the X-ray report attached?), flags from the rules engine (room over cap, waiting period), which TPA, and simple text flags from the summary (does it state why admission was needed?).
- **Model:** XGBoost classifier with SHAP explanations. The top three SHAP features map to fixes through a lookup table, for example `missing_xray_report` → "attach the pre-op X-ray report".

### How you'll measure them

| What | Metric | Good sign |
| --- | --- | --- |
| Cost estimator | Average error in ₹; share of bills under the high estimate | About 90% of bills fall under the 90th-percentile estimate |
| Denial-risk model | ROC-AUC; recall on denials | Catches most denials without flagging every claim |
| Document reader | Field accuracy on 30 hand-checked documents | Errors are flagged by low confidence, not silent |
| Whole system | First-pass approval rate with vs without the denial-risk agent | A clear jump: your headline number |

**Two cautions:**

- Split train and test data by admission date, not randomly, so the test looks like the future.
- Trained on synthetic data, the models learn the patterns you planted. Say this openly: the pipeline is the product, and in a real hospital the models retrain on its own claim history.

## 8. Data plan

Generate about 1,000 synthetic admissions with planted problems, and use real public documents for policies, medical codes and scheme rates.

| Dataset | Source | Size | Notes |
| --- | --- | --- | --- |
| Policy wordings | Public policy PDFs from 3–4 Indian health insurers | 3–4 policies | Real; pick ones with room caps and sub-limits |
| ICD-10 codes | WHO's public code list | About 200 codes | Only those for your procedures |
| PM-JAY package rates | National Health Authority's published package list | 30–50 packages | For the scheme mode |
| Procedures | Your own list | 25 | Appendix removal, knee replacement, cataract, angioplasty, C-section and so on |
| Patients and policies held | Python Faker | 1,000 | Age, city, policy, sum insured, start date |
| Admissions and bill lines | Generator script with a cost range per procedure | 1,000 admissions, about 15 lines each |  |
| Discharge summaries, lab reports | LLM-written from the structured data, rendered to PDF | 1,000; 50 as scanned images | Scans test the vision reader |
| Claim outcomes | Rules engine + TPA profiles + some random noise | 1,000 | Labels for the denial model |

### Planted problems

| Problem | Share of cases |
| --- | --- |
| Clean | 45% |
| Room rent over cap | 15% |
| Missing document | 12% |
| Non-payables billed | 10% |
| Sub-limit exceeded | 8% |
| Waiting period breached | 6% |
| Coding mismatch | 4% |

**Key trick:** generate the structured data first, then write documents from it. You then know the true value of every field in every document, which gives you free test labels for the document reader.

### Database tables

```sql
patients(patient_id, name, age, gender, city)
policies(policy_id, insurer, product, sum_insured, start_date)
rules(rule_id, policy_id, type, value_json, clause_id, verified)
admissions(admission_id, patient_id, policy_id, procedure, icd10, room_category, admit_date, discharge_date, tpa_id)
bill_lines(line_id, admission_id, category, description, amount)
documents(doc_id, admission_id, type, file_path, extracted_json)
claims(claim_id, admission_id, state, requested, approved, risk_score)
claim_messages(msg_id, claim_id, type, sender, round, body_json, created_at)
audit_log(event_id, claim_id, actor, action, input_hash, output_json, created_at)
```

## 9. Responsible AI controls

No claim, appeal or patient message leaves the system without a human, and every agent decision carries its evidence. CGI scores this explicitly, so make each control visible in the UI.

| Control | How it works | Where judges see it |
| --- | --- | --- |
| Human gates | Pre-auth, enhancement, discharge and appeal all need approval | Approval queue |
| Confidence thresholds | Below the threshold, an agent escalates instead of guessing | Flagged items in the queue |
| Cited reasoning | A finding without a source is rejected by the validator | Agent trace on each claim |
| Calculated money | All amounts come from the rules engine | Deduction preview |
| Data masking | Names, phone and ID numbers are swapped for tokens before any LLM call, then restored | Audit log shows masked prompts |
| Audit trail | Every agent and human action logged with time, input and output | Audit tab |
| Bounded autonomy | Agents can't send; three negotiation rounds at most; a stop switch per claim | Claim settings |
| Fairness check | Denial model excludes gender and religion; denial rates compared by age band and gender on the test set | Dashboard panel |
| Safe fallback | If the LLM is down, claims park in a manual queue | Status banner |

One more rule: patient messages never mention fraud suspicions. Those go only to staff.

## 10. Extensions that add depth

These turn ClaimPilot from a demo into a platform. Build them in this order; the room advisor comes first because it is the strongest moment in the demo.

1. **Room advisor (patient agent):** at admission, it runs Ramesh's numbers live for each room option and tells the patient, in plain words, that the cheaper room saves about ₹47,200. Judges feel this one.
2. **Learning loop:** every query and denial updates what the hospital knows about each TPA, and the denial model retrains. Show it on screen: "TPA-A now needs an X-ray report for orthopaedic claims, learned from 6 denials."
3. **PM-JAY mode:** under the government's Ayushman Bharat scheme, hospitals are paid fixed package rates, not itemised bills. Swap the rulebook and the same agents handle scheme claims. This is your government angle.
4. **National claims standard:** India is moving health claims onto a common exchange (the National Health Claims Exchange) that uses the FHIR data standard. Package claims as FHIR Claim resources. Check the current spec before mentioning it on stage.
5. **Patient notes in Hindi and Kannada:** the patient agent translates its cost note.
6. **Fraud screen:** already on the insurer side; add a hospital-side version that catches billing mistakes before the insurer calls them fraud.

## 11. Tech stack and repo

Stick to tools you've already used, so hackathon hours go into the system rather than learning.

| Layer | Choice | Why |
| --- | --- | --- |
| Agents | LangGraph | Supervisor, parallel branches, state; you know it |
| LLM | One API model with vision (Gemini or Claude) | Reads scans and writes letters |
| Output checks | Pydantic | Enforces the shared output format |
| Backend | FastAPI | Quick REST plus a WebSocket for live agent traces |
| Database | Postgres (SQLite if setup eats time) | Claims, messages, audit |
| Policy search | ChromaDB | Clause-level search |
| Models | XGBoost + SHAP | Cost, denial risk, explanations |
| Documents | PyMuPDF, Tesseract as backup | PDF text and scans |
| Frontend | React + Tailwind | Board, trace, queue, dashboard |
| Run | Docker Compose | One command on demo day |

```text
claimpilot/
  backend/
    agents/
      hospital/   intake.py coder.py policy.py estimator.py preauth.py denial_risk.py
                  enhancement.py discharge.py query_responder.py appeal.py reconcile.py patient.py
      insurer/    adjudicator.py medical_review.py fraud.py query_writer.py
      orchestrator.py
    rules/        engine.py rule_types.py tests/test_ramesh.py
    rag/          ingest.py search.py
    models/       train_cost.py train_denial.py fixes_map.py
    exchange/     messages.py
    db/           schema.sql
    api/          main.py
  data/
    generator/    patients.py admissions.py documents.py outcomes.py
    policies/     real policy PDFs
    rules/        extracted rule JSON per policy
  frontend/       React app
  docker-compose.yml
```

## 12. UI screens

Six screens, each built to carry one moment of the demo.

| Screen | Shows | Demo moment |
| --- | --- | --- |
| Claims board | Claims as cards in columns by state, moving live | The system working on its own |
| Claim detail | Timeline of every agent step, its reasoning and its source | "Watch the agents think" |
| Approval queue | Waiting gates with risk score, findings, and approve, edit or reject | Human oversight |
| Insurer console | The simulated TPA's view and the message thread | The negotiation |
| Patient view | Out-of-pocket estimate and room advice in plain language | Ramesh's ₹47,200 |
| Dashboard | First-pass rate, turnaround, rupees recovered, top denial reasons | Your numbers |

Stream agent steps to the claim detail screen over a WebSocket, so judges see each agent act as it happens rather than a finished result.

## 13. Demo script and pitch

Five minutes: one human story, three cases, one headline number, one line for CGI.

1. **0:00–0:40 Hook:** Ramesh picked a room ₹8,000 dearer and paid ₹47,200 more. Nobody told him. Hospitals and patients lose like this every day.
2. **0:40–1:00 What it is:** the one-line pitch and the lifecycle diagram.
3. **1:00–1:50 Case 1, clean appendix surgery:** admission to insurer approval in under a minute. Shows speed.
4. **1:50–3:00 Case 2, knee replacement:** the denial-risk agent catches a missing X-ray report and the room cap, the room advisor shows the patient the saving, a human approves the fixed package. Shows intelligence.
5. **3:00–4:10 Case 3, the fight:** the insurer partly denies, the appeal agent argues back citing a clause, the human approves, the insurer accepts in round two. Shows multi-agent negotiation and oversight.
6. **4:10–4:40 Numbers:** the dashboard, with and without the denial-risk agent.
7. **4:40–5:00 Close:** "ClaimPilot plugs into a hospital's billing system and India's claims exchange. It's the kind of integration CGI delivers, and we'd love to build it with you."

Record a backup video of the full demo in case the live run breaks.

### Likely judge questions

| Question | Your answer |
| --- | --- |
| What if the LLM invents a number? | It can't. Money comes from the rules engine; the LLM only reads and writes. |
| Isn't the insurer scripted to lose? | It has hidden strictness profiles and denies real gaps. Hospital agents learn them from denials. |
| Is the data real? | Policies, medical codes and scheme rates are real. Patients are synthetic for privacy; models retrain on a hospital's own history. |
| How would this integrate? | APIs in front, FHIR format for claims, one state machine per claim. |
| What about patient privacy? | Data is masked before LLM calls, everything is audited, and nothing leaves without approval. |
| Why multi-agent and not one big prompt? | Each step has different tools, data and failure modes. Separate agents can be tested, swapped and audited on their own. |

## 14. Build plan

Today is for data, rules, models and design; hackathon day is for wiring the agents and the demo. First, confirm in the shortlisted-teams group whether code written before the event is allowed.

- [ ] Ask the organisers whether pre-built code is allowed

### Today: Person A (data and models)

- [ ] Download 3–4 real policy PDFs; fix the list of 25 procedures
- [ ] Write the generator: patients, admissions, bill lines, planted problems
- [ ] Generate documents from the data (LLM text, rendered to PDF; 50 as scans)
- [ ] Build the rules engine; the Ramesh test must return ₹1,85,800
- [ ] Train the cost and denial-risk models; save their metrics

### Today: Person B (agents and UI)

- [ ] Extract rule JSON from the policies and check it by hand
- [ ] Set up the repo, database schema and state machine
- [ ] Write each agent's prompt and output schema
- [ ] Write the three TPA strictness profiles
- [ ] Sketch the six screens

### Tonight: both

- [ ] Fix the three demo cases and their expected outcomes
- [ ] Write and time the pitch script
- [ ] Sleep at least six hours

If pre-built code isn't allowed, keep today to data files, rule JSON, prompts, sketches and the pitch, and train the models in the first hour tomorrow.

### Hackathon day (8 hours)

1. **Hours 0–1:** database, rules engine, data loaded.
2. **Hours 1–3:** document reader, coder, policy agent, pre-auth agent and orchestrator. Case 1 runs end to end.
3. **Hours 3–4.5:** denial-risk agent, insurer agents and the message protocol. Cases 2 and 3 run.
4. **Hours 4.5–6:** claims board, claim detail trace, approval queue, patient view.
5. **Hours 6–7:** dashboard; run a 50-claim batch for your numbers; fix bugs.
6. **Hours 7–8:** rehearse twice, record the backup video, stop changing code.

**If time runs short, cut in this order:** FHIR format, PM-JAY mode, reconciliation, fraud screen, enhancement agent.

**Never cut:** the denial-risk agent, insurer negotiation, human gates and the room advisor. Those are what win.
