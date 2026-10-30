import streamlit as st

from core.auth import require_login
from core.db import fetch_all, init_db
from core.portal import list_own_complaints
from core.ui import apply_demo_style, render_hero
from core.geo import configure_staff_location, staff_location_snapshot
from core.geo_ui import render_geography_picker
from core.geo import STAFF_ROLES
from core.staff_dashboard_ui import render_staff_dashboard


def _render_area_selection(user, prefix):
    selected = render_geography_picker(prefix)
    if st.button("Show complaints from this location", key=f"{prefix}-submit", type="primary"):
        try:
            outcome = configure_staff_location(user, *selected)
            st.session_state["staff-active-area-label"] = (
                f"{outcome['area_name']} · {outcome['city_name']}, {outcome['state_name']}"
            )
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


init_db()
user = require_login()
apply_demo_style()

if user["role"] == "public":
    # Explicit H1 landmark for accessibility compliance (ArgosX / WCAG 2 AA)
    st.markdown("<h1 style='font-size: 2rem; margin-bottom: 0.5rem;'>NagrikSetu Resident Home</h1>", unsafe_allow_html=True)
    mine = list_own_complaints(user["id"])
    with st.container(border=True, key="resident-complaint-total"):
        st.metric("Total complaints submitted", len(mine))
else:
    # Explicit H1 landmark for staff session
    st.markdown("<h1 style='font-size: 1.85rem; margin-bottom: 0.5rem;'>NagrikSetu Staff Workspace</h1>", unsafe_allow_html=True)
    if user["role"] in STAFF_ROLES:
        location_state = staff_location_snapshot(user)
        st.subheader("Choose your service area")
        st.caption("Your complaint list and dashboard totals will follow the service area selected here.")
        if any(row["actor_email"] == "system:demo-admin" for row in location_state["assigned"]):
            st.info("DEMO ACCOUNT — pre-approved synthetic assignment. Access covers all areas in Gwalior. Select a service area below to focus the dashboard.")
        elif not location_state["assigned"]:
            st.info("Select a service area below. Complaints from that location become available immediately, subject to your staff role and department.")
        with st.container(border=True):
            _render_area_selection(user, "staff-area-select")
        render_staff_dashboard(user, service_areas=location_state["assigned"])
        st.caption("NagrikSetu · Synthetic prototype · SLA targets and ownership data are demo assumptions.")
        st.stop()
    else:
        visible_cases = []
    render_hero(
        "Government workspace",
        "NagrikSetu",
        "Review civic reports, record reasoned decisions, and follow each case's history.",
    )
    counts = {"cases": len([row for row in visible_cases if row["parent_id"] is None]),
              "sub_issues": len([row for row in visible_cases if row["parent_id"] is not None])}
    languages = len({row["language"] for row in visible_cases})
    visible_ids = [row["id"] for row in visible_cases]
    open_challenges = 0
    uncertain = 0
    if visible_ids:
        placeholders = ",".join("?" for _ in visible_ids)
        metrics = fetch_all(
            "SELECT (SELECT COUNT(*) FROM challenges h WHERE h.status='open' "
            f"AND h.complaint_id IN ({placeholders})) AS open_challenges, "
            "(SELECT COUNT(*) FROM complaints c WHERE c.id IN (" + placeholders + ") AND "
            "(SELECT s.abstained FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1)=1) AS uncertain",
            visible_ids + visible_ids)[0]
        open_challenges, uncertain = metrics["open_challenges"], metrics["uncertain"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Complaints in selected areas", counts["cases"] or 0)
    c2.metric("Needs human review", uncertain)
    c3.metric("Open resident review requests", open_challenges)
    st.caption(f"Linked sub-issues: {counts['sub_issues']} · languages represented: {languages}. "
               "All counts are restricted to the areas you selected.")

    how_col, expect_col = st.columns([1.35, 1], gap="large")
    with how_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">Staff workflow</div>', unsafe_allow_html=True)
            st.subheader("Follow a complaint through the review process")
            steps = [
                ("01", "Capture", "Preserve the original report and the fields the intake rules can identify."),
                ("02", "Inspect", "Review the suggestion, evidence, uncertainty, and ownership assumptions."),
                ("03", "Consider a split", "Check proposed sub-issues and confirm only when they make sense."),
                ("04", "Resolve", "Record a reasoned decision or challenge outcome."),
            ]
            for number, title, detail in steps:
                number_col, detail_col = st.columns([.16, 1], vertical_alignment="top")
                number_col.markdown(f"<div class='nf-score'>{number}</div>", unsafe_allow_html=True)
                detail_col.markdown(f"**{title}**  {detail}")
                st.write("")
    with expect_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">Review principles</div>', unsafe_allow_html=True)
            st.subheader("Human judgement stays central")
            st.markdown(
                """
                - Suggestions are prompts for review, not final assignments.
                - Uncertain routes remain visible for a person to assess.
                - Split proposals are saved for staff review; sub-issues are created only after a reviewer confirms.
                """
            )
            st.caption("NagrikSetu uses synthetic demo data; department rules are assumptions.")

    st.markdown('<div class="nf-section-kicker">Quick links</div>', unsafe_allow_html=True)
    links = [
        ("pages/6_Inbox.py", "Inbox", "📥"),
        ("pages/2_Review.py", "Review", "🔎"),
        ("pages/3_Challenge.py", "Challenge", "⚖️"),
        ("pages/7_Replay.py", "Case replay", "🎞️"),
        ("pages/4_Evaluation.py", "Evaluation", "📊"),
    ]
    quick_cols = st.columns(len(links))
    for column, (path, label, icon) in zip(quick_cols, links):
        column.page_link(path, label=label, icon=icon, width="stretch")

if user["role"] != "public":
    st.divider()
    st.caption("NagrikSetu · Synthetic prototype · Prototype authentication, not production-grade.")