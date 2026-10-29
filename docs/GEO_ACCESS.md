# Geographic complaint access

## How access is granted

1. A resident or staff member authenticates using the existing sign-in flow.
2. Ordinary staff select State → City/district → Area on the overview page, enter a reason and submit an assignment request. Matching complaints remain hidden until a configured geography administrator approves it.
3. Approval and revocation append events to `government_area_events`, recording staff/admin identity, location, timestamp and reason. Selecting a scope already granted through the protected allowlist returns the existing approved scope.
4. When `NAGRIKSETU_DEMO_MODE=1` and `NYAYAFLOW_SEED_DEMO_STAFF=1`, only the exact synthetic operator01, reviewer01 and auditor01 accounts may receive the labelled city-wide Gwalior grant. Both flags default off; ordinary staff continue to require administrator approval.

Set `NYAYAFLOW_GEO_ADMIN_EMAILS` to a comma-separated list of approved administrator emails in the trusted deployment environment. Do not commit real staff emails to the repository. `data/geo_admin_allowlist.csv` is a local prototype option and must be protected in deployments.

The demo staff emails are synthetic identities, not employment verification. Verification codes are simulated in demo mode. Replace the sample administrator configuration with an authorized, protected deployment configuration before using real accounts.

## Complaint handling

The resident portal offers cascading geography selectors. Residents may leave a lower level unknown; the system preserves only the known levels and marks the service area missing. Such cases remain in the restricted geography-admin queue until triaged; staff must select a known area to see area-matched complaints.

The selected state/city/area is checked server-side and persisted as an append-only access event. Inbox, review, challenges, duplicate matching/decisions, attachments, replay, analytics, and audit exports enforce or query this scope. Staff still need an active staff identity; operators remain department-filtered, and auditors remain read-only. Residents continue to query complaints by their authenticated account ID.

Area options live in `data/geo_coverage.json`. They are sample prototype geography covering Madhya Pradesh, Uttar Pradesh, Maharashtra, and Punjab, including Gwalior areas Morar and Gole Ka Mandir. They are not an authoritative ward-boundary or municipal jurisdiction dataset; verify and replace them with approved city data before operational deployment.

## Legacy records and limits

The schema migration adds structured location columns but does not infer geography for existing complaints. Complaints without structured area fields remain unassigned and are hidden from ordinary area-scoped staff until reviewed by a geography administrator. Existing user role data is preserved. Authentication and access checks are prototype-grade; protect deployment secrets, use a production identity provider, and review the data protection model before real resident data is stored.
