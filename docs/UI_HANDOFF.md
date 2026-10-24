# UI handoff

The refinement pass only added functional wiring, built from the existing components and styles. This note lists what the API now offers and what visual work remains.

## Data the screens can use

| Need | Where |
| --- | --- |
| Evidence behind a verdict | `GET /api/claims/{id}` → `evidence[]`: `{by, assertion, verdict, reason, passages[{doc_id, page, text, finding, kind}], problems[]}` |
| Open a cited page | `GET /api/claims/{id}/documents/{doc_id}/file#page=N` |
| What to fix | `tasks[]`: `{kind: attach, attach_or_correct, correct, read_scan, review_document, find_evidence, issue, fix, source, doc_type?, doc_id?, field?}` |
| Attach / correct / recheck | `POST .../documents` (base64 PDF), `POST .../corrections`, `POST .../recheck`; `actions` lists which are allowed now |
| Last recheck | `last_recheck`: `{resolved, remaining, new, draft_before, draft_after}` |
| Draft versions | `pending_draft.{version, hash}`; `drafts[]`: `{version, hash, type, stage, round, status: pending, superseded, rejected, sent, dropped, approved_by}` |
| Approve the exact draft | `POST .../decision` with `draft_hash`. A stale hash returns 409; a repeat returns `duplicate: true` |
| Provenance | `attachments[]` (who, when, sha256, pages), `corrections[]`, `timeline[]` |
| Money for display | `totals.billed`, `totals.deducted_by_message[]` (aligned with `thread`), appeal `disputed_total`. All computed by the rules engine |
| Room advisor | `POST .../patient-note` → `note.{options, recommended, basis, clause, assumptions}` |
| Health and fallback | `GET /api/ready` → `models`, `llm.mode` (`live` or `fallback`) and `reason`, `interrupted_claims`, `problems` |
| Resume | `POST /api/claims/{id}/resume` |
| Policy configurations | `GET /api/policy-configs` (source, verified, rule types, fingerprint); start a case under one with `policy_config` |
| Import | `POST /api/imports` `{format, content}` → imported, duplicates, or row-level errors |
| Dashboard | `GET /api/analytics` (KPIs, pipeline, first answers by stage and TPA, deductions by clause and policy split, oversight, risk histogram, LLM mode, benchmark) |

## Remaining visual work

- An evidence viewer: a side panel showing the cited page with the passage highlighted. Today findings link to the PDF page in a new tab.
- A draft history and diff view (v1 → v2) using `drafts[]` and `last_recheck`.
- A correction form, for conflicts and scans. The API exists; the UI only offers attach and recheck.
- A health banner when `llm.mode` is `fallback` or `interrupted_claims` isn't empty, with a *Resume* button.
- Screens for policy configurations and imports. Both are API-only today.
- A simulation label on the insurer desk and the benchmark panel that is more prominent than the current text.
- Dashboard: date-range and insurer filters in one row above the charts, a dark theme (the app has none yet), and a richer chart hover style if wanted.
