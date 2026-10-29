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
    if st.button("Show complaints from this location", key=f"{prefix}-submit"):
        try:
            outcome = configure_staff_location(user, *selected)
            st.success(f"{outcome['area_name']} selected. Matching complaints are now available "
                       "within your staff role and department.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


init_db()
user = require_login()
apply_demo_style()

if user["role"] == "public":
    render_hero(
        "Civic grievance support",
        "NagrikSetu",
        "Report a civic problem in Hindi, English or Hinglish, add a photo if you have one, and follow your complaint with a case ID. A staff member reviews every suggested department before anything is final.",
    )
    mine = list_own_complaints(user["id"])
    report_col, track_col = st.columns(2, gap="large")
    with report_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">Start here</div>', unsafe_allow_html=True)
            st.subheader("Report a problem")
            st.write("Tell us what happened and where. Add a photo if it helps explain the issue.")
            st.page_link("pages/10_Public_Portal.py", label="Report a problem", icon="📝", width="stretch")
    with track_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">Your cases</div>', unsafe_allow_html=True)
            st.subheader("Track my complaints")
            st.metric("Your complaints", len(mine))
            st.page_link("pages/8_Request_Review.py", label="Track my complaints", icon="📬", width="stretch")

    st.write("")
    how_col, expect_col = st.columns([1.35, 1], gap="large")
    with how_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">A simple path</div>', unsafe_allow_html=True)
            st.subheader("How it works")
            steps = [
                ("01", "Describe", "Tell us what happened and where, in Hindi, English or Hinglish. Add a photo if you can."),
                ("02", "We suggest", "The system suggests which department may handle it. If it is unsure, it says so and may ask you one question."),
                ("03", "A person reviews", "Staff check the suggestion and make the final decision. Nothing is closed automatically."),
                ("04", "Track and ask again", "Follow your case ID under My complaints. If something looks wrong, you can request a review."),
            ]
            for number, title, detail in steps:
                number_col, detail_col = st.columns([.16, 1], vertical_alignment="top")
                number_col.markdown(f"<div class='nf-score'>{number}</div>", unsafe_allow_html=True)
                detail_col.markdown(f"**{title}**  {detail}")
                st.write("")
    with expect_col:
        with st.container(border=True):
            st.markdown('<div class="nf-section-kicker">Clear expectations</div>', unsafe_allow_html=True)
            st.subheader("What to expect")
            st.markdown(
                """
                - Suggestions are not final assignments.
                - If the system is unsure, your complaint waits for a person to decide.
                - You can ask for a review of any of your complaints.
                - Photos are stored with your complaint and are not analysed automatically.
                """
            )
            st.caption("NagrikSetu is a prototype using synthetic demo data. Department rules are assumptions, not an official charter.")
else:
    if user["role"] in STAFF_ROLES:
        location_state = staff_location_snapshot(user)
        st.subheader("Your selected service areas")
        if location_state["assigned"]:
            if any(row["actor_email"] == "system:demo-admin" for row in location_state["assigned"]):
                st.info("DEMO ACCOUNT — pre-approved synthetic assignment. Access covers all areas in Gwalior.")
            st.dataframe([{"State": row["state_name"], "City": row["city_name"], "Area": row["area_name"],
                           "Access source": "Self-selected" if row["actor_email"] == user["email"] else row["actor_email"],
                           "Record": row["reason"]}
                          for row in location_state["assigned"]], hide_index=True, width="stretch")
        else:
            st.info("Select a service area below. Complaints from that location become available immediately, "
                    "subject to your staff role and department.")
        st.subheader("Choose another service area" if location_state["assigned"] else "Choose a service area")
        _render_area_selection(user, "staff-area-select")
        render_staff_dashboard(user)
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

st.divider()
st.caption("NagrikSetu · Synthetic prototype · Prototype authentication, not production-grade.")
