# Demo staff service-area access fix — 2026-10-29

**Superseded for ordinary staff:** a later update allows staff to select any configured service area immediately without an administrator request. The opt-in city-wide grants described below remain available to the three synthetic demo accounts but are no longer needed for location access. See `STAFF_AREA_SELF_SELECTION_2026-10-29.md`.

## Root cause and phase 0 findings

The empty staff interface was an authorization result. `core/geo.py::scoped_complaints` (line 473) builds the server-side list from the latest approved `government_area_events` rows; with no scope it returns no complaints. `core/geo.py::require_complaint_area_access` (line 407) independently rechecks the same scope for case reads and writes. Those checks run from staff dashboard and service methods, not just from a banner.

The append-only assignment event schema is `db/schema.sql` (lines 30–50): it records user, structured state/city/area, event, actor, reason and a database timestamp; UPDATE/DELETE are blocked. Staff request area access from `core/geo.py::request_area_assignment` (line 238), rendered from `pages/0_Home.py` (lines 89–101). An authorized reviewer listed in `data/geo_admin_allowlist.csv` can approve through `core/geo.py::approve_area_request` (line 332) and `pages/12_Area_Access_Admin.py`. The current configured demo geography administrator is `reviewer05@staff.nagriksetu-demo.example`; the three target demo emails are already present as synthetic directory entries in `data/staff_directory.csv`.

The public form is `pages/10_Public_Portal.py` (lines 30–31, 54, 72–86). It stores the entered location string, normalized matching key, and structured geography fields in the same complaint row through `core/db.py::insert_public_submission` (line 148). Visibility used exact state/city/area codes; it did not match free-text location aliases. Gole Ka Mandir selected from the picker stored `mp / mp-gwalior / mp-gwalior-gole-ka-mandir` and therefore matched an identical approved area row. An unknown area or no area code did not match such an area grant. The Gwalior picker originally listed seven localities.

This project is a Streamlit application and has no HTTP API or HTTP status-code contract. The server-side `scoped_complaints` and `list_staff_cases` services are the actual staff-list boundary. Verification below exercised the public form and each signed-in staff view through Streamlit `AppTest`, plus direct service calls; it did not invent an API endpoint.

## Changes

- Added `NYAYAFLOW_SEED_DEMO_STAFF`, an explicit opt-in whose code default is off. Seeding also requires `NAGRIKSETU_DEMO_MODE=1` and an active, registered user whose email and role match one of exactly three entries: `operator01` / operator, `reviewer01` / reviewer, and `auditor01` / auditor under `@staff.nagriksetu-demo.example`.
- A successful login for an existing demo staff account, or completion of its existing synthetic email-code registration, appends one city-wide Gwalior `approved` event to `government_area_events`. The actor is `system:demo-admin`; the reason identifies the synthetic demo grant; `created_at` records the approval time. A `BEGIN IMMEDIATE` check makes repeated or concurrent seeding idempotent. Prior revocation remains authoritative and is never undone by login or the seed script.
- The city-wide scope uses the existing assignment event table with `area_code='*'`; it does not add a separate authorization path. Normal query and detail checks interpret the row as matching only `mp / mp-gwalior`. This includes complaints in that city with a known area, an unmatched/missing area, or location text `unknown`. Complaints lacking a state/city are not attributed to Gwalior and remain in the configured geography administrator's unassigned queue. If the opt-in/demo flags are turned off, the synthetic grant is hidden from active scope while its append-only history remains stored.
- The exact demo operator can see all departments only within the seeded Gwalior city scope while the explicit seed flag and demo mode are enabled. This is needed because the example streetlight complaint confidently routes to Electricity while `operator01` is a Roads operator. Other operators keep the existing department filter. The demo operator still cannot resolve citizen review requests; reviewer resolution remains reason-required and auditor writes remain denied.
- `known_area_for_location` matches exact area names after the existing NFC, trim, whitespace-collapse and casefold normalization. It also recognizes the explicit alias `गोले का मंदिर`. The public form uses it only with selected Madhya Pradesh and Gwalior fields, and stores the user-entered location string unchanged. Unknown or non-matching text does not create an area code.
- Added the requested Gwalior locality options: DD Nagar, Phool Bagh, Maharaj Bada, Jayendraganj, Station Road, Jhansi Road, Tansen Road and Sirol Road, alongside the prior seven. A city-wide assignment covers all fifteen without fragile per-area grants.
- The staff home shows **“DEMO ACCOUNT — pre-approved synthetic assignment”** and lists **“All areas (city-wide)”**. Real accounts without an approval retain the warning and request form. The geography admin screen describes the city grant clearly and can revoke it through the existing append-only event flow.
- Added `scripts/seed_demo_staff_areas.py` for explicit idempotent seeding of already registered demo accounts. `.env.example` and README document local-only configuration.

## Verification

The prior full audit, before this issue's changes, completed **162 passed** in the root suite and **159 passed** in the acceptance runner. It documented the observed empty list and the existing geography gate; no pre-change HTTP/API reproduction is possible because no API exists.

Current commands, run with the project virtual environment and a writable workspace temporary directory:

```powershell
$base='D:\IIITM PS\_pytest_tmp_demo_access'
$env:TEMP=$base
$env:TMP=$base
& '.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp $base --tb=short

$env:PYTEST_ADDOPTS='--tb=short'
& '.venv\Scripts\python.exe' -m scripts.run_acceptance
```

Actual final outputs:

- Root pytest suite: **168 passed in 73.89s**.
- Acceptance runner: **165 passed in 73.59s**; AT-01 through AT-06 all passed. `tests/results.md` and `docs/acceptance_results.json` were refreshed.
- Focused geography and role checks: **13 passed** after the final scoped department exception. The public-form-to-three-login UI regression passed inside the final full run and acceptance runner.
- Streamlit `AppTest` submitted the exact streetlight example through the public form, signed in through the form as each isolated synthetic account, and asserted that same case ID appeared in all three `Case review` views. Separate public-form tests submit Gole Ka Mandir and assert the exact original location remains stored while the normalized English/Hindi alias resolves to its structured area.
- Regression assertions also verify the exact streetlight complaint, city-scoped unknown/missing-area and abstained Hindi records appear for operator, reviewer and auditor; a Hinglish routed record appears; an unknown-city record stays out of the Gwalior scope; operator cannot resolve a citizen review request; auditor cannot write; reviewer resolves with a reason; new non-demo staff remain empty; and disabling either demo flag hides the seed grant.
- The seed idempotency test verifies one approval row, actor, reason and timestamp; administrator revocation is appended and not reversed by another seed attempt.
- Explicit CLI smoke check on an isolated database with three registered synthetic accounts: first `python -m scripts.seed_demo_staff_areas` printed `Appended 3 demo staff area approval(s).`; the second printed `Appended 0 demo staff area approval(s).`; a direct SQL read returned `[{'actor_email': 'system:demo-admin', 'approvals': 3}]`.

## Local demo commands

Already registered demo accounts can receive their grant at next successful login. Newly registered target accounts receive it immediately after the existing simulated email-code verification. The checked-in staff directory does not contain passwords; do not assume a shared password. For accounts already registered, use the password set during their existing registration.

In PowerShell, opt in only for a local demo process:

```powershell
$env:NAGRIKSETU_DEMO_MODE='1'
$env:NYAYAFLOW_SEED_DEMO_STAFF='1'
& '.venv\Scripts\python.exe' -m scripts.seed_demo_staff_areas
& '.venv\Scripts\python.exe' -m streamlit run app.py
```

The seed command only finds registered users. If a target email has not registered, use the existing Government staff registration flow for that directory email; the demo verification code is shown in the app. Sign in as `operator01@staff.nagriksetu-demo.example`, `reviewer01@staff.nagriksetu-demo.example`, or `auditor01@staff.nagriksetu-demo.example` with the password chosen at registration.

## Limits and risks

- `NYAYAFLOW_SEED_DEMO_STAFF` is absent/off by default, and `.env.example` is not loaded automatically. Keep it unset in any shared or production environment. Even if someone sets it, the code accepts only the exact synthetic emails and expected directory roles. All resulting rows are synthetic and visibly labelled.
- A city-wide assignment does not assign a department to a complaint. Operators still see confident routed complaints only when their department matches; abstained and missing-suggestion cases are shown as manual-review work. A demo operator assigned to Roads will not see a confidently routed Electricity case.
- A complaint with no structured state or city cannot be safely attributed to Gwalior from a text guess. It remains available to the existing geography-admin unassigned queue instead of being disclosed to every city staff member.
- The local login screen still has the existing simulated verification behavior in demo mode. No demo password was added or preseeded. Authentication, TLS, database encryption and production identity remain prototype concerns documented in the main audit.
- Direct HTTP status-code tests and screenshots were not produced: there is no HTTP API, and the final UI evidence is automated Streamlit `AppTest` execution. The tests use disposable databases; the configured user database was not seeded by this audit.
