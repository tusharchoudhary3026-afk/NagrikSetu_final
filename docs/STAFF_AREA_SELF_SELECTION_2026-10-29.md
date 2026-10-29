# Staff area self-selection fix (2026-10-29)

The Overview no longer asks staff to submit an area-access request or wait for a geography administrator. A signed-in, active staff-directory account can select Gole Ka Mandir (or another configured area) and immediately see matching complaints after the page refreshes. Selections persist in the additive `staff_selected_areas` table.

All server-side checks still validate staff identity and role. Operator reads/actions remain department-scoped; reviewer and auditor capabilities remain as defined by their roles. The old administrator-request page was removed from app navigation. Existing pending Morar requests are historical and do not block selecting Gole Ka Mandir.

Regression coverage submits a public Gole Ka Mandir complaint, selects that area as ordinary staff without an approval step, and asserts the complaint is returned. The public complaint visibility test also verifies immediate visibility to a matching operator/reviewer, including an abstained complaint for reviewer manual review.

Geography options remain synthetic sample data. This change makes selection self-service, not verification of a municipal assignment or an authoritative boundary.
