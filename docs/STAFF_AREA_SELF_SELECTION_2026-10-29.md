# Staff geography-access clarification (2026-10-29)

An earlier UI change allowed every active staff account to activate a location without approval. That policy was reverted to match the current requirement: ordinary staff area requests stay pending until an authorized geography administrator approves them. The request and approval are recorded in append-only `government_area_events`.

Only the exact synthetic demo accounts `operator01@staff.nagriksetu-demo.example`, `reviewer01@staff.nagriksetu-demo.example` and `auditor01@staff.nagriksetu-demo.example` can receive an automatic city-wide Gwalior grant. Both `NAGRIKSETU_DEMO_MODE=1` and `NYAYAFLOW_SEED_DEMO_STAFF=1` are required; the latter defaults off. The grant is labelled as demo data. Operators remain department-filtered; reviewers can take permitted actions; auditors remain read-only.

The latest AppTest covers the real UI request path for an ordinary reviewer, confirms the case stays hidden while the request is pending, and existing end-to-end tests confirm the three exact demo roles see the submitted Gole Ka Mandir case. Geographic options remain synthetic sample data, not authoritative boundaries.
