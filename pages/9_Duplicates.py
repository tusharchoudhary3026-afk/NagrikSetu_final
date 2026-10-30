import difflib
import streamlit as st

from core.config import SIM_LABEL
from core.db import fetch_all, init_db
from core.duplicates import DECISION_REASON_CODES, find_candidates, record_decision
from core.geo import is_geography_admin, scoped_complaints
from core.privacy import mask_text
from core.ui import apply_demo_style, render_hero, require_permission


def render_word_diff(text1: str, text2: str) -> tuple[str, str]:
    """Return inline HTML with differing tokens highlighted."""
    words1 = text1.split()
    words2 = text2.split()
    matcher = difflib.SequenceMatcher(None, words1, words2)
    h1, h2 = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            h1.extend(words1[i1:i2])
            h2.extend(words2[j1:j2])
        elif tag in ("replace", "delete"):
            h1.append(f"<span style='background-color: #FDECEC; color: #B42318; padding: 1px 4px; border-radius: 4px;'>{' '.join(words1[i1:i2])}</span>")
            if tag == "replace":
                h2.append(f"<span style='background-color: #E6F4EC; color: #1F6B48; padding: 1px 4px; border-radius: 4px;'>{' '.join(words2[j1:j2])}</span>")
        elif tag == "insert":
            h2.append(f"<span style='background-color: #E6F4EC; color: #1F6B48; padding: 1px 4px; border-radius: 4px;'>{' '.join(words2[j1:j2])}</span>")
    return " ".join(h1), " ".join(h2)


init_db()
user = require_permission("can_review")
apply_demo_style()
render_hero("Human review", "Duplicate candidates",
            "Compare grounded signals and record a reasoned human decision. Similarity never merges cases.")
st.warning(SIM_LABEL)
st.caption("TF-IDF may miss cross-language Hindi-English reports. Candidate scores are similarity signals, not proof.")

complaints = [row for row in scoped_complaints(
    user, include_unassigned=is_geography_admin(user.get("email", "")))
              if row.get("parent_id") is None and row.get("is_active", 1)]
if not complaints:
    st.info("No complaints are available in your authorized service areas.")
    st.stop()

selected = st.selectbox("Complaint", complaints,
                        format_func=lambda row: f"{row['case_id']} · {row.get('language') or 'unknown'} · "
                        f"{mask_text(row['original_text'])[:65]}")
st.subheader("Selected complaint")
st.write(mask_text(selected["original_text"]))
st.caption(f"{selected.get('channel') or 'unknown'} · {selected.get('language') or 'unknown'} · "
           f"{selected.get('location') or 'unknown'} · {selected.get('created_at') or 'time unknown'}")

allowed_ids = sorted({int(row["id"]) for row in complaints})
allowed_marks = ",".join("?" for _ in allowed_ids)
links = fetch_all(
    "SELECT l.*,a.case_id AS case_a,b.case_id AS case_b,a.original_text AS text_a,"
    "b.original_text AS text_b,a.location AS location_a,b.location AS location_b,"
    "(SELECT r.actor FROM duplicate_reviews r WHERE r.complaint_id=l.complaint_id "
    "AND r.candidate_complaint_id=l.candidate_complaint_id ORDER BY r.id DESC LIMIT 1) AS decided_by "
    "FROM incident_links l JOIN complaints a ON a.id=l.complaint_id "
    "JOIN complaints b ON b.id=l.candidate_complaint_id "
    f"WHERE ((l.complaint_id=? AND l.candidate_complaint_id IN ({allowed_marks})) OR "
    f"(l.candidate_complaint_id=? AND l.complaint_id IN ({allowed_marks}))) ORDER BY l.id DESC",
    [selected["id"], *allowed_ids, selected["id"], *allowed_ids])
if links:
    st.subheader("Existing incident links and history")
    for item in links:
        status = "confirmed" if item["active"] else "unlinked / prior link retained"
        other_text = item["text_b"] if item["complaint_id"] == selected["id"] else item["text_a"]
        other_case = item["case_b"] if item["complaint_id"] == selected["id"] else item["case_a"]
        with st.container(border=True):
            st.markdown(f"**{other_case} · {status} · decided by {item['decided_by'] or 'recorded staff'}**")
            st.write(mask_text(other_text))
            if item["active"]:
                reason = st.text_input("Reason to unlink", key=f"unlink-reason-{item['id']}")
                if st.button("Unlink incidents", key=f"unlink-{item['id']}"):
                    other_id = item["candidate_complaint_id"] if item["complaint_id"] == selected["id"] else item["complaint_id"]
                    try:
                        record_decision(selected["id"], other_id, "unlink", user["role"], reason,
                                        actor_email=user.get("email"),
                                        reason_code=DECISION_REASON_CODES["unlink"], actor_user=user)
                        st.success("Unlink decision appended to both complaint histories.")
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))

st.subheader("Ranked candidates")
candidates = find_candidates(selected["id"], limit=5, actor_user=user)
if not candidates:
    st.info("No candidates with a text, issue, or location signal were found.")
for candidate in candidates:
    with st.container(border=True):
        st.markdown(f"### {candidate['case_id']} · {candidate['candidate_outcome']}")
        
        # Word difference comparison
        diff1, diff2 = render_word_diff(mask_text(selected["original_text"]), mask_text(candidate["original_text"]))
        left, right = st.columns(2)
        left.markdown("**Selected complaint (Diff)**")
        left.markdown(f"<div style='line-height: 1.6;'>{diff1}</div>", unsafe_allow_html=True)
        left.caption(f"{selected.get('channel') or 'unknown'} · {selected.get('language') or 'unknown'} · "
                     f"{selected.get('location') or 'unknown'}")
        
        right.markdown(f"**Candidate complaint (Diff)**")
        right.markdown(f"<div style='line-height: 1.6;'>{diff2}</div>", unsafe_allow_html=True)
        right.caption(f"{candidate.get('channel') or 'unknown'} · {candidate.get('language') or 'unknown'} · "
                      f"{candidate.get('location') or 'unknown'}")
        
        st.write({"similarity signal": candidate["score"], "text similarity": candidate["text_sim"],
                  "shared terms": candidate["shared_terms"],
                  "shared issue labels": candidate["shared_issue_labels"],
                  "location relation": candidate["location"],
                  "time gap days": candidate["time_difference_days"],
                  "guard warnings": candidate["guard_flags"]})
        st.caption(f"Decision history: {candidate['decision']} · active link: {candidate['active_link']}")
        if candidate["guard_flags"]:
            st.warning("Guarded candidate: this may be a different incident. Check the underlying details before deciding.")
        action = st.selectbox("Human decision", ["same_incident", "different"],
                              format_func=lambda value: "Confirm same incident" if value == "same_incident"
                              else "Not the same incident", key=f"decision-{candidate['complaint_id']}")
        reason = st.text_input("Reason (required)", key=f"reason-{candidate['complaint_id']}")
        if st.button("Record decision", key=f"save-{candidate['complaint_id']}"):
            try:
                record_decision(selected["id"], candidate["complaint_id"], action, user["role"], reason,
                                actor_email=user.get("email"),
                                reason_code=DECISION_REASON_CODES[action], actor_user=user)
                st.success("Decision recorded on both complaints. Neither complaint was merged or hidden.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))