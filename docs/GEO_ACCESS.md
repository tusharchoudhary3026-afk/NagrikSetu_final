# Geographic complaint visibility

Signed-in staff select State → City/district → Area from the Overview page. The selection is active immediately and persists in `staff_selected_areas`; no geography-administrator request or approval is needed. Multiple areas can be selected for the same account. Refreshing the staff page reloads those scopes.

Server-side complaint reads and review actions continue to validate the authenticated active staff identity, role, and selected service-area scope. Operator complaint lists and actions remain restricted to the assigned department; reviewers can see cases in selected areas and auditors remain read-only. Public users still see only their own complaints. Selecting a location grants access to complaints whose structured state/city/area matches that selection; it does not grant access to unassigned complaints.

Existing administrator approvals and old pending requests in `government_area_events` remain as historical records. They do not block a staff member from selecting that same location in the Overview. The old area-request administration page is no longer in staff navigation.

The optional synthetic city-wide Gwalior assignment remains available only when `NAGRIKSETU_DEMO_MODE=1` and `NYAYAFLOW_SEED_DEMO_STAFF=1`, for the exact demo operator01, reviewer01, and auditor01 accounts. It is not required for ordinary staff to select a location.

Area choices are in `data/geo_coverage.json`; they are simulated sample geography for Madhya Pradesh, Uttar Pradesh, Maharashtra, and Punjab, not authoritative ward boundaries. Complaints with missing state/city/area remain restricted to geography administration and are not included when staff select a known location. Review and replace the sample geography and access policy before any operational deployment.
