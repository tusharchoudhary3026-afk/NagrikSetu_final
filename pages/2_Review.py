import json
import streamlit as st
from streamlit.errors import StreamlitPageNotFoundError

from core.attachment import get_attachments, read_attachment
from core.config import REASON_CODES, REVIEW_ACTIONS, SIM_LABEL
from core.corrections import review_correction
from core.db import fetch_all, init_db
from core.duplicates import DECISION_REASON_CODES, find_candidates, record_decision
from core.explanation import generate_grounded_explanation
from core.ledger import history
from core.ownership import check_suggestions, detect_bounce, repeat_reassignment
from core.privacy import mask_text
from core.reviews import submit_review
from core.splitter import confirm_split, merge_back
from core.intake import ISSUE_CATEGORIES, process as process_intake
from core.router_baseline import route as route_baseline
from core.suggestions import selected_department
from core.ui import require_permission
from core.geo import is_geography_admin, scoped_complaints

init_db()
user = require_permission("can_review")
st.title("Human review")
st.warning(SIM_LABEL)
st.caption("Review access is limited to your verified geographic areas and current role permissions. "
           "Routing suggestions remain recommendations until a person records a decision.")

rows = [{key: row[key] for key in ("id", "case_id", "parent_id", "language", "original_text", "location",
                                    "state_name", "city_name", "area_name", "issue", "requested_action", "missing_fields")}
        for row in scoped_complaints(user, include_unassigned=is_geography_admin(user.get("email", "")))]
if not rows:
    st.info("No complaints yet. Use Intake first.")
    st.stop()
pick = st.selectbox("Complaint", rows,
                    format_func=lambda r: f"{r['case_id']} | {r['language']} | {mask_text(r['original_text'])[:50]}")
st.write("**Original complaint (personal details masked):**", mask_text(pick["original_text"]))
area_label = " · ".join(value for value in (pick.get("state_name"), pick.get("city_name"), pick.get("area_name")) if value)
st.caption(f"Service area: {area_label or 'Unassigned · restricted manual-review queue'} · "
           f"Extracted issue: {pick.get('issue') or 'unknown'} · "
           f"Requested action: {pick.get('requested_action') or 'unspecified'} · "
           f"Missing fields: {pick.get('missing_fields') or 'none recorded'}")

sugs = fetch_all("SELECT * FROM suggestions WHERE complaint_id=? ORDER BY id DESC LIMIT 1", (pick["id"],))
suggested = None
top3 = []
if sugs:
    top3 = json.loads(sugs[0]["top3"])
    suggested = selected_department({"top3": top3, "abstained": bool(sugs[0]["abstained"])})
    evidence = json.loads(sugs[0]["evidence"] or "{}")
    st.write("**Department suggestions:**", top3)
    st.caption(f"Confidence score: {sugs[0]['confidence']} (uncalibrated) · Manual review: {bool(sugs[0]['abstained'])}")
    for item in top3:
        dept = item.get("dept")
        st.caption(f"Evidence for {dept}: {evidence.get(dept, evidence.get('matched_terms', []))}")
        try:
            explanation = generate_grounded_explanation(pick["original_text"], dept)
            st.info(f"{', '.join(explanation['grounded_keywords'])} matches {explanation['issue_type']}. "
                    f"Rule source: {explanation['rule_source']} ({explanation['verification']}).")
        except ValueError as exc:
            st.warning(f"No grounded explanation available for {dept}: {exc}")

for overlap in check_suggestions([item["dept"] for item in top3], pick["original_text"]):
    status = str(overlap.get("verification", "UNKNOWN")).strip().upper()
    verification_label = "unverified rule" if status in {"ASSUMPTION", "UNKNOWN", "UNVERIFIED"} else status
    st.warning(f"Possible ownership overlap: {overlap['owner']} / {overlap['overlap_owner']} · "
               f"{overlap['issue_type']} · {verification_label} · Source: {overlap['source']}. "
               "This flag is not a decision about who is responsible.")

children = fetch_all("SELECT id,original_text FROM complaints WHERE parent_id=? AND is_active=1 ORDER BY id", (pick["id"],))
pending_splits = fetch_all("SELECT proposal_json FROM split_proposals WHERE parent_id=? AND status='proposed'",
                           (pick["id"],))
if pending_splits and not children:
    st.subheader("Proposed sub-issues · not yet confirmed")
    try:
        proposal = json.loads(pending_splits[0]["proposal_json"])
    except (TypeError, json.JSONDecodeError):
        proposal = {"parts": []}
    st.caption("The citizen's original complaint remains intact. Review the spans, then confirm or edit the proposal.")
    edited_parts = []
    keep_keys = []
    source_text = pick["original_text"]
    for index, part in enumerate(proposal.get("parts", [])):
        with st.container(border=True):
            st.markdown(f"**Sub-issue {index + 1} · {part.get('issue', 'unknown')}**")
            st.write(part.get("text", ""))
            st.caption(f"Source characters {part.get('start')}–{part.get('end')}")
            key = f"review-split-keep-{pick['id']}-{index}"
            keep = st.checkbox("Keep this sub-issue", value=True, key=key)
            keep_keys.append(key)
            if not keep:
                continue
            text_key = f"review-split-text-{pick['id']}-{index}"
            snippet = st.text_area("Edit sub-issue text", value=part.get("text", ""), key=text_key)
            issue_key = f"review-split-issue-{pick['id']}-{index}"
            categories = ["unknown", *(category for category, _ in ISSUE_CATEGORIES)]
            issue = st.selectbox("Issue category", categories,
                                 index=(categories.index(part.get("issue")) if part.get("issue") in categories else 0),
                                 key=issue_key)
            route = route_baseline(snippet)
            dept = selected_department(route)
            if route["abstained"]:
                st.warning("No supported department suggestion for this edited text; manual review remains required.")
            else:
                st.write(f"Department suggestion: {dept} · evidence: {route['evidence'].get(dept, [])}")
                choices = [item["dept"] for item in route.get("top3", [])]
                if len(choices) > 1:
                    selected = st.selectbox("Reviewer-adjusted suggestion (evidence-backed)", choices,
                                            index=choices.index(dept),
                                            key=f"review-split-department-{pick['id']}-{index}")
                    if selected != dept:
                        ranked = list(route["top3"])
                        ranked.sort(key=lambda item: item["dept"] != selected)
                        route["top3"] = ranked
                        route["confidence"] = ranked[0]["score"]
            edited_parts.append({**part, "text": snippet, "issue": issue, "suggestion": route})
    added_text = st.text_input("Add a sub-issue (optional; must be copied exactly from the original text)",
                               key=f"review-split-added-{pick['id']}")
    if added_text.strip():
        added_text = added_text.strip()
        start = source_text.find(added_text)
        if start >= 0:
            added_rec = process_intake(added_text)
            added_route = route_baseline(added_text)
            edited_parts.append({"text": added_text, "issue": added_rec["issue"],
                                 "suggestion": added_route, "start": start,
                                 "end": start + len(added_text), "issue_evidence": added_rec["issue_evidence"]})
        else:
            st.warning("An added sub-issue must be an exact span of the original complaint.")
    edit_reason = st.text_input("Reason for editing this split (required only if you change it)",
                                key=f"review-split-reason-{pick['id']}")
    if st.button("Confirm split", key=f"confirm-split-{pick['id']}", type="primary"):
        try:
            if len(edited_parts) < 2:
                raise ValueError("Keep at least two sub-issues, or leave the complaint intact.")
            cursor = 0
            for part in edited_parts:
                start = source_text.find(part["text"], cursor)
                if start < 0:
                    raise ValueError("Edited sub-issue text must remain an exact span of the original complaint.")
                part["start"], part["end"] = start, start + len(part["text"])
                cursor = part["end"]
            original_parts = proposal.get("parts", [])
            changed = (len(edited_parts) != len(original_parts) or any(
                current.get("text") != original.get("text") or current.get("issue") != original.get("issue")
                or current.get("suggestion") != original.get("suggestion")
                for current, original in zip(edited_parts, original_parts)))
            if changed and not edit_reason.strip():
                raise ValueError("Enter a reason before saving split edits.")
            confirm_split(pick["id"], pick["case_id"], edited_parts, user["role"],
                          actor_email=user.get("email"), actor_user=user,
                          reason=edit_reason if changed else None, edited=changed)
            st.success("The reviewer-confirmed sub-issues were saved under the same case ID.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

if children:
    st.subheader("Confirmed sub-issues")
    st.dataframe([{"id": child["id"], "text": mask_text(child["original_text"])} for child in children],
                 hide_index=True, width="stretch")
    merge_reason = st.text_input("Reason for merging back (required)", key=f"merge-split-reason-{pick['id']}")
    if st.button("Merge split back into the intact complaint"):
        try:
            count = merge_back(pick["id"], user["role"], merge_reason,
                               actor_email=user.get("email"), actor_user=user)
            st.success(f"Merged {count} child views back. All child records and history were retained.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

st.subheader("Human decision")
action = st.radio("Action", REVIEW_ACTIONS, horizontal=True)
final = st.text_input("Final department", value=suggested or "")
reason = st.text_area("Reason (required for edit, transfer, and escalation)")
code = st.selectbox("Reason code", REASON_CODES)
if st.button("Record decision", type="primary"):
    try:
        submit_review(pick["id"], user["role"], action, final, reason, code,
                      actor_email=user.get("email"), actor_user=user)
        st.success("Decision and any correction example were saved together. Earlier history remains intact.")
    except (ValueError, PermissionError) as exc:
        st.error(str(exc))

st.subheader("Photo attachments")
attachments = get_attachments(pick["id"], user["role"], actor_user=user)
for attachment in attachments:
    try:
        st.image(read_attachment(attachment["id"], user["role"], actor_user=user),
                 caption=f"photo attachment: stored only, not analysed · {attachment['original_name']}")
    except ValueError as exc:
        st.warning(str(exc))
if not attachments:
    st.caption("No photos attached.")

st.subheader("Possible duplicate or related incidents")
st.caption("Similarity signals rank candidates; they are not probabilities or duplicate decisions. "
           "Hindi-English cross-language matching can miss related reports. Similarity never changes status or routing.")
candidates = find_candidates(pick["id"], actor_user=user)
if candidates:
    candidate = st.selectbox("Candidate", candidates,
        format_func=lambda c: f"{c['case_id']} · {c['candidate_outcome']} · score {c['score']:.2f} · {c['decision']}")
    st.caption(f"Candidate outcome: {candidate['candidate_outcome']} · review signal only; does not change case status or routing.")
    left, right = st.columns(2)
    left.markdown("**Selected complaint**")
    left.write(mask_text(pick["original_text"]))
    right.markdown(f"**Candidate {candidate['case_id']}**")
    right.write(mask_text(candidate["original_text"]))
    st.write({"shared_terms": candidate["shared_terms"], "shared_issue_labels": candidate["shared_issue_labels"],
              "text similarity signal": candidate["text_sim"], "same issue": candidate["same_issue"],
              "location relation": candidate["location"], "time_match": candidate["time_match"],
              "time_difference_days": candidate["time_difference_days"],
              "missing_fields": candidate["missing_fields"], "conflicts": candidate["conflicts"]})
    decision = st.selectbox("Human decision", ["same_incident", "related", "different", "needs_info", "unlink"],
                            format_func=lambda x: {"same_incident": "Confirm same incident", "related": "Related, not duplicate",
                                "different": "Different incident", "needs_info": "Need more information", "unlink": "Unlink / reopen"}[x])
    duplicate_reason_code = DECISION_REASON_CODES[decision]
    st.caption(f"Structured reason code: `{duplicate_reason_code}`")
    duplicate_reason = st.text_input("Decision reason")
    if st.button("Save duplicate review"):
        try:
            record_decision(pick["id"], candidate["complaint_id"], decision, user["role"], duplicate_reason,
                            actor_email=user.get("email"), reason_code=duplicate_reason_code, actor_user=user)
            st.success("Duplicate review recorded. Original complaints and decisions remain available.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
else:
    st.info("No possible candidates found. Missing location or time may limit matching.")
try:
    st.page_link("pages/9_Duplicates.py", label="Open duplicate review", icon="🔎")
except StreamlitPageNotFoundError:
    # Also allow this page to be smoke-tested directly outside app.py navigation.
    st.caption("Open Duplicates from the Review menu.")

st.subheader("Ledger (append-only)")
h = history(pick["id"], actor_user=user)
st.dataframe([{**event, "reason": mask_text(event.get("reason", ""))} for event in h],
             width="stretch", hide_index=True)
bounce, repeats = detect_bounce(h), repeat_reassignment(h)
if bounce:
    st.warning(f"Transfer pattern flagged (no blame): {bounce['pattern']}")
if repeats:
    st.warning(f"Repeat reassignment flag: {repeats['moves']} owner changes ({repeats['path']}); no fault is inferred.")

st.subheader("Correction examples awaiting verification")
st.caption("Reviewer corrections are not used for training unless a reviewer verifies them. Retraining is offline and never automatic.")
pending = fetch_all("SELECT c.id,c.text,c.wrong_dept,c.correct_dept,c.reason,c.reason_code,c.label_source "
                    "FROM corrections c WHERE NOT EXISTS (SELECT 1 FROM correction_reviews r "
                    "WHERE r.correction_id=c.id) ORDER BY c.id")
if not pending:
    st.caption("No unverified corrections.")
for correction in pending:
    with st.expander(f"Correction #{correction['id']} · {correction['wrong_dept']} → {correction['correct_dept']}"):
        st.write(mask_text(correction["text"]))
        st.caption(f"Reason code: {correction['reason_code']} · Provenance: {correction['label_source']}")
        verification_reason = st.text_input("Verification note", key=f"verify-note-{correction['id']}")
        col1, col2 = st.columns(2)
        for column, status, label in ((col1, "verified", "Verify label"), (col2, "rejected", "Reject label")):
            if column.button(label, key=f"{status}-{correction['id']}"):
                try:
                    review_correction(correction["id"], user.get("email", "government"), user["role"], status,
                                      verification_reason, actor_email=user.get("email"), actor_user=user)
                    st.success("Verification outcome appended to the audit history.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
