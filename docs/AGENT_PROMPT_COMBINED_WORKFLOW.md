# Codebase-specific implementation prompt

Paste this into an AI coding agent opened at the NagrikSetu repository root:

```text
You are implementing features in the existing NagrikSetu codebase. Work directly in this repository; do not create a standalone demo or replace the application architecture.

## First, establish the actual state

Read README.md, docs/CODE_AUDIT_2026-10-29.md, docs/STAFF_DASHBOARD.md, docs/LEDGER_INTEGRITY.md, db/schema.sql, core/db.py, and the relevant services/pages/tests before changing code. Inspect git status and preserve any user changes. This is a Python 3.12 + Streamlit + SQLite application. The service layer lives in core/, screens in pages/, additive database setup/migrations are in core/db.py, and there is no separate HTTP API.

Inspect these existing implementations before editing: core/splitter.py, core/router_baseline.py, core/router_ml.py, core/explanation.py, core/ownership.py, core/duplicates.py, core/safety.py, core/ledger.py, core/staff_dashboard.py, core/staff_dashboard_ui.py, pages/1_Intake.py, pages/2_Review.py, pages/9_Duplicates.py, and data/responsibility_table.csv. Do not assume the requested features are absent: compound proposals, evidence-based keyword routing, grounded explanations, responsibility overlap and transfer-cycle warnings, duplicate candidates, scoped staff review, and the append-only decision ledger already exist. Verify behavior in the current checkout and close concrete integration gaps.

## Desired combined workflow

1. Preserve the citizen's exact original complaint and language. Keep normalization, transcript, extracted fields, and reviewer corrections as separate provenance; never silently replace the source report.
2. Detect compound complaints and propose exact source spans as independent sub-issues. Each part needs its own issue evidence, supported department candidates, abstention state, and grounded explanation. Keep the parent intact and unassigned as a whole until an authorized reviewer confirms or edits the split. Preserve parent/child links and histories; require a reason for edits and merge-back.
3. Suggest departments with the live rule-based baseline in core/router_baseline.py. Show the matched complaint words and the corresponding responsibility-table source and verification status. TF-IDF in core/router_ml.py is an evaluation comparison, not a live production route. Do not claim LLM or trained-model routing. Abstain and request clarification when evidence is insufficient; do not convert a top candidate into a final assignment.
4. Check responsibility overlaps with core/ownership.py and transfer cycles from ledger history. Surface possible conflicts without blaming or assigning a department. Responsibility rows marked ASSUMPTION remain visibly unverified.
5. Rank possible duplicate/related incidents with core/duplicates.py using text, issue, time, location, and conflict evidence. Scores are candidate-ranking signals, never probabilities or proof. A reviewer must confirm, reject, mark related, request information, or unlink with a reason. Never merge or hide the original cases automatically.
6. Run explainable safety triage on the parent and each confirmed sub-issue. Use data/safety_lexicon.json and core/safety.py; keep multilingual phrases and context rules in data, not UI code. Return hazard category, exact words from the original report, rule IDs/version, context, score, severity band, and human-review requirement. Handle negation, resolution, historic/unrelated mentions, uncertainty, and mixed hazards per occurrence. A missing match cannot prove safety. Thresholds and critical overrides must be labeled as prototype defaults, not validated safety thresholds. No image analysis is in scope.
7. Put the safety assessment, split/routing evidence, ownership and duplicate signals, and prior human decisions into the existing authorized review experience. Require reasons for safety overrides/dismissals. Persist generated assessments and human decisions as append-only ledger events, preserving the original system assessment, actor, timestamp, reason, and updated value. Enforce role and geographic scope in service code as well as the UI.
8. Keep speech-to-text as an editable, user-confirmed transcript; keep photos as encrypted attachments only. Do not add paid APIs, external inference services, or automatic duplicate merges.

## Implementation rules

- Reuse existing services, schema conventions, auth, geography scoping, ledger, and Streamlit UI. Add only additive/idempotent migrations when persistence cannot use an existing table.
- Keep the live route deterministic and explainable. Do not invent training data, validation results, official responsibility, emergency response, or confirmed hazards.
- Keep original text and prior decisions immutable. Require reasons for important reviewer corrections and preserve a traceable event history.
- Validate text and action inputs. Keep citizen personal information masked in staff presentation unless authorized access requires it.
- Keep user-facing copy clear that the system recommends and a human decides. Do not imply that a safety score is a probability or a certified assessment.
- Add regression coverage for English/Hindi/Hinglish, compound parent/child behavior, per-issue routes and abstentions, grounded sources, ownership conflict and A→B→A history, duplicate candidate safeguards and reversible decisions, safety active/negated/resolved/historical/uncertain context, reviewer permission/reason enforcement, and append-only history.

## Verification and completion report

Run focused tests first, then `python3 -m pytest -q` and `python3 -m scripts.run_acceptance` when practical. Check all Streamlit pages still import. Report the exact files changed, actual lexicon/rule and evaluation dataset counts, database changes, commands and results. Separate working behavior from assumptions, synthetic-only results, pending validation, and known limitations. Do not describe synthetic benchmark results as field accuracy. If a command cannot run, say which command and the observed blocker. Do not stop after proposing a plan; implement and verify the work in this repository.
```
