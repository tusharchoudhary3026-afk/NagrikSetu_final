# NagrikSetu

NagrikSetu is a human-reviewed civic grievance routing prototype. Its demo records are synthetic and its department ownership rules are assumptions. It does not automatically reject, close, deprioritize, assign, or merge complaints.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Required for encrypted photo uploads; keep this stable and private.
$env:NYAYAFLOW_ATTACHMENT_KEY = "<stable-64-character-key-from-your-secrets-manager>"
python -m scripts.init_db
python -m streamlit run app.py
```

## Authentication and roles

Public users can register and sign in with email/password or an Indian mobile number/password. Phone registration requires a one-time verification step. Staff register only through `data/staff_directory.csv`; their directory role and department are assigned server-side and a staff email code is required. Ordinary staff request a State, City and Area assignment, which must be approved before matching complaints are shown. The exact three synthetic demo accounts can receive a labelled, opt-in city-wide Gwalior assignment. Operators remain limited by their department, reviewers can review cases, and auditors are read-only.

The checked-in staff directory and all verification codes are synthetic. The app starts in **DEMO MODE**, displays simulated codes on screen, and sends no real email or SMS. Staff directory identities do not establish real employment. Existing accounts and roles are retained; public signup cannot choose a staff role. The existing public role is named `public` in the database and corresponds to the citizen-facing interface. A separate, opt-in `NYAYAFLOW_SEED_DEMO_STAFF=1` flag grants city-wide Gwalior access only to the exact synthetic operator01, reviewer01, and auditor01 accounts; the flag defaults off and must never be set in production.

After staff sign-in, the Overview route opens the staff complaint operations dashboard; the public report form is not shown as the staff landing page. Staff select service areas themselves; complaint reads remain server-filtered by active role and selected geography, and operator inboxes also remain department-filtered. Auditors are read-only. The dashboard provides status/SLA cards, scoped search and filters, case routing and evidence, safety/ownership/duplicate signals, open review requests, an immutable timeline, and reasoned ledger actions. See `docs/STAFF_DASHBOARD.md` for role scope and demo assumptions.

For local use, install the requirements and start `python -m streamlit run app.py`; schema migrations run on app startup. For the full authentication behavior and migration notes, see [docs/AUTH_NOTES.md](docs/AUTH_NOTES.md). Passwords are stored as bcrypt hashes (or scrypt if bcrypt is unavailable). Verification codes are stored as keyed hashes. Login attempts and authentication audit events persist in SQLite. These are prototype controls, not a production security certification.

Signed-in staff can select one or more service areas from Overview; matching complaints are available immediately and the selection persists across refreshes. The existing role checks remain active, and operator inboxes remain restricted to the operator's department. Geography values are synthetic prototype choices, not authoritative municipal boundaries. Optional city-wide Gwalior demo assignments remain limited to the exact synthetic operator01, reviewer01, and auditor01 accounts.

The application uses one SQLite database path: `db/nyayaflow.db` by default, overridden only by `NYAYAFLOW_DB_PATH`. Relative overrides resolve from the project root. This Streamlit app has no separate HTTP API or API base URL. [.env.example](.env.example) documents optional environment variables; Streamlit does not load it automatically, so export values in your VS Code terminal.

The decision ledger and its SHA-256 integrity sidecar are append-only. Auditors can run **Verify ledger integrity** from Case replay. This detects ordinary changes to stored event content; it is not a signed or tamper-proof log. See [docs/LEDGER_INTEGRITY.md](docs/LEDGER_INTEGRITY.md) for the additive migration, backup and rollback steps.

Real email/SMS delivery, MFA, password reset, SQLite encryption at rest, and production HTTPS/identity configuration are ROADMAP items. Disabling demo mode without setting `NAGRIKSETU_AUTH_SECRET` produces a clear configuration error; even with that secret, staff and phone verification cannot proceed until real providers are implemented.

## Test

```powershell
python -m pytest -q
python -m scripts.run_acceptance
python -m scripts.evaluate
python -m scripts.retrain_offline
```

The frozen test split is grouped by complaint family. Evaluation fits TF-IDF on train, tunes its abstention threshold on validation, and reports both systems on the held-out test rows. The dataset is small, synthetic, and AI-labelled; scores are preliminary and do not establish real-world performance or fairness.

## Current workflow

- Multilingual typed intake preserves original text and stores normalized text separately. Unknown location or issue information prompts clarification.
- Rule-based routing returns evidence and abstains when it has no matching evidence. The TF-IDF router is an evaluation comparison, not a live replacement.
- Compound cases can be split only after a reviewer confirms the proposed sub-issues. Merge-back hides child views but preserves records and history.
- Reviewers accept, edit, clarify, escalate, transfer, raise or resolve challenges, and record structured reasons in an append-only event ledger.
- Duplicate candidates show TF-IDF text similarity, shared issue labels, a normalized location relation, time gap, and guard evidence. A reviewer must confirm or reject with a reason; confirmation and unlink/rejection events are appended for both complaints, and every original complaint remains available. The score is a ranking signal, not a probability or verdict. Cross-language Hindi-English matching may miss reports.
- JPEG, PNG, and WEBP attachments are size/type-checked, metadata-stripped, encrypted at rest with AES-256-GCM, and available to government staff only. Set `NYAYAFLOW_ATTACHMENT_KEY` to 64 hex characters (32 bytes) before enabling photo uploads. Keep it in a secrets manager and retain a protected backup; losing it makes stored photos unreadable. No image analysis occurs.
- Before enabling uploads on a database with older photo attachments, back up the database and `data/attachments`, set the same stable key, then run `python scripts/migrate_attachment_encryption.py`. Legacy photos remain blocked from the UI until migrated.
- Speech-to-text is an optional browser trial on intake. Users must disclose and consent to browser speech recognition, review and edit the returned text, and explicitly confirm it before processing. Audio is not stored by NagrikSetu; browser providers may process it. Hindi-English mixed speech quality is unvalidated.
- A configurable safety rubric creates attention flags for human review; it is team-proposed and does not assign risk or priority.
- Its 12-case English/Hindi/Hinglish check set reports an explicit numerator and denominator, and is labelled a synthetic rubric sanity check rather than field recall.
- Reassignment and hotspot reports show time windows, observed counts, baselines, denominators, and synthetic-data labels. These counts do not establish fault.
- Reviewer corrections are saved with provenance. Only explicitly verified corrections are exposed to a future offline training experiment; nothing retrains or deploys automatically.
- A reviewer can run a guarded offline experiment once at least 10 verified corrections span at least two departments. It reuses the frozen test set, excludes near-matches to held-out text, and saves or deploys no model.

## Scope limits

Speech-to-text remains a trial feature; actual microphone and language-quality validation is still required. Existing photo files created before the encryption migration are marked legacy plaintext and cannot be viewed through the app; re-upload them using the secure intake flow. The SQLite complaint/decision database has no application-level encryption, and HTTPS, backups, retention, and production-grade identity infrastructure must be verified and configured for the deployment. The duplicate benchmark has only 40 synthetic AI-authored/AI-reviewed pairs (20 development, 20 frozen test), and is not independently labelled ground truth. Thresholds are development-only; similarity is not proof. Do not put real or sensitive case data into this prototype.

See [data/DATASET_CARD.md](data/DATASET_CARD.md) for dataset provenance and limitations.
See [docs/AUTH_NOTES.md](docs/AUTH_NOTES.md) for the authentication implementation, simulated pieces, and roadmap.
