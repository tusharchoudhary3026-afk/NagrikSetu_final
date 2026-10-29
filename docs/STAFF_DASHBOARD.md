# Government staff operations dashboard

## Built

- The existing signed-in Overview route now renders a government operations dashboard for staff. Public users retain the resident overview and complaint portal.
- Operator/officer inbox rows are filtered in `core.geo.scoped_complaints` by both approved geography and the staff department stored on the account. Department ownership follows the latest non-empty human final department in the ledger; otherwise an unabstained routing suggestion is only used to populate the appropriate review inbox. A route suggestion is never treated as a final decision.
- Reviewer and auditor scope remains limited to their approved geography and role permissions. Reviewer cross-department review within that approved area is identified in the dashboard. Auditors are read-only. No role gets a global complaint list through the dashboard.
- Case details include masked original text, normalized text, language/channel, extracted fields, routing history/evidence and uncertainty, safety flags, source and verification label for ownership overlaps, duplicate candidates, open challenges, human-confirmed sub-issues, authorized photos, and a read-only submission/suggestion/ledger timeline.
- The Review group includes a scoped Duplicates page. Candidate evidence is grounded in the two complaints' text/issue/location fields, low-similarity and different-issue/time cases remain visibly guarded, and confirm/reject/unlink decisions require a reason and append ledger events to both cases. Existing complaint rows are never merged or hidden. Cross-language duplicate matching is not established.
- Summary cards show new, pending, overdue, awaiting clarification, and resolved cases. Inbox controls support text, status, area, category, safety, ownership conflict, submission-date, overdue, and deadline sorting.
- `NAGRIKSETU_DEMO_SLA_DAYS` configures prototype deadlines from submission, clamped to 1–90 days; default is 7. The UI labels these as demo assumptions, not official service guarantees. SLA state never mutates a case or makes a decision.
- Dispatch, clarification, resolution, and transfer are explicit, authenticated staff actions. Each requires a note, and transfers also require a structured reason code and target department. Actions append actor, timestamp, reason, prior/new state, and department transition to the existing append-only ledger. Clarification creates an open staff challenge without replacing the complaint text. Dispatch records the action locally; no field unit is contacted.
- A transfer changes which operator department can see the case. Every transfer requires a reason; a bounce/repeated-reassignment pattern is surfaced for review. No automatic transfer, rejection, closure, or deprioritization was added. Rejection is not supported by the existing review workflow and is not offered.
- Ward/area hotspot summaries are limited to the authorized case set and operator department. They show recent pending counts, recent and prior-period submission counts, period, synthetic/observed status, and a low-sample label. Hotspots are exploratory summaries, not validated trend claims.

## Assumptions and limits

- There is no authoritative SLA schedule in the repository. The 7-day default is a demo assumption that must be replaced with approved service-specific rules before operational use.
- Reviewer access to multiple departments is the existing reviewer role capability, but remains bounded by the reviewer's verified geography and is called out in the UI. Operator access is department-specific. Auditor access is read-only and geographically scoped.
- Intake stores the submitted original text and a separate normalized text. Speech input requires an explicit confirmation before processing; audio is not retained, and a distinct transcript-confirmation event is not stored.
- Duplicate and ownership results are suggestions. Responsibility ownership labels remain assumptions where the underlying table says so. Field dispatch is not integrated with an external service.
- Staff, cases, location coverage, routing rules, and trend examples in the checked-in demo are synthetic. No government authority or real municipal SLA is implied.

## Verification

Dashboard service, access-control, action-ledger, hotspot, SLA, and Streamlit shell coverage is in `tests/test_staff_dashboard.py`; existing geography, workflow, and app-shell checks remain in the full suite. See `docs/TEST_REPORT.md` for the latest commands and actual results.
