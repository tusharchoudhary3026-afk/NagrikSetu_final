"""Streamlit presentation for the authenticated staff operations dashboard."""
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from .auth import verify_permission
from .config import REASON_CODES, feature_enabled
from .intake import ISSUE_CATEGORIES, normalize_location
from .intake_corrections import correct_intake_fields
from .privacy import mask_text
from .staff_dashboard import (get_staff_case_detail,
                              get_ward_hotspots, list_staff_cases, _parse_time,
                              perform_staff_action)
from .ui import render_hero


def _date_label(value):
    parsed = _parse_time(value)
    if not parsed:
        return "Date unavailable"
    return parsed.strftime("%d %b %Y, %H:%M UTC")


def _case_label(case):
    location = case.get("area_name") or case.get("location") or "Area not recorded"
    return f"{case['case_id']} · {case['issue'] or 'Uncategorized'} · {location} · {case['status']}"


def _service_area_label(area):
    return f"{area['area_name']} · {area['city_name']}, {area['state_name']}"


def render_staff_dashboard(user, service_areas=None):
    """Render a location-scoped, actionable staff workspace."""
    from .geo import CITY_WIDE_AREA_CODE, area_options

    service_areas = service_areas or []
    area_by_label = {_service_area_label(area): area for area in service_areas}
    active_assignment = None
    is_city_wide = False
    render_hero("Government staff workspace", "Complaint operations",
                "Review complaints for one selected service area and record accountable actions.")

    st.subheader("Location and access scope")
    scope_col, location_col = st.columns(2, gap="medium")
    if area_by_label:
        selected_label = st.session_state.get("staff-active-area-label")
        if selected_label not in area_by_label:
            active_assignment = max(service_areas, key=lambda row: str(row.get("created_at") or ""))
            selected_label = _service_area_label(active_assignment)
            st.session_state["staff-active-area-label"] = selected_label
        selected_label = scope_col.selectbox("Assigned service area", list(area_by_label),
                                             key="staff-active-area-label")
        active_assignment = area_by_label[selected_label]
        is_city_wide = active_assignment.get("area_code") == CITY_WIDE_AREA_CODE
    selected_location_name = None
    location_filter = {}
    if active_assignment and is_city_wide:
        city_areas = (area_options().get(active_assignment["state_code"], {})
                      .get("cities", {}).get(active_assignment["city_code"], {}).get("areas", {}))
        location_key = (f"staff-ward-filter-{active_assignment['state_code']}-"
                        f"{active_assignment['city_code']}")
        selected_area_code = location_col.selectbox(
            "Location to review", [None, *city_areas],
            format_func=lambda code: "Choose a ward / area" if code is None else city_areas[code],
            key=location_key,
        )
        if selected_area_code is None:
            active_area_name = "a selected ward / area"
        else:
            selected_location_name = city_areas[selected_area_code]
            active_area_name = selected_location_name
            location_filter = {
                "state_code": active_assignment["state_code"],
                "city_code": active_assignment["city_code"],
                "area_code": selected_area_code,
            }
    elif active_assignment:
        active_area_name = active_assignment["area_name"]
        selected_location_name = active_area_name
        location_col.caption(f"Location filter: {selected_location_name}")
        location_filter = {
            "state_code": active_assignment["state_code"],
            "city_code": active_assignment["city_code"],
            "area_code": active_assignment["area_code"],
        }
    else:
        # Fail closed if this UI is ever called without an approved location.
        active_area_name = "an assigned service area"
        st.info("Select an approved service area above to load complaints.")
    current_scope = tuple(location_filter.get(key) for key in ("state_code", "city_code", "area_code"))
    if st.session_state.get("staff-dashboard-last-scope") != current_scope:
        for key in ("staff-inbox-search", "staff-inbox-status", "staff-inbox-issue",
                    "staff-inbox-sort", "staff-inbox-safety", "staff-inbox-overlap",
                    "staff-inbox-overdue", "staff-inbox-dates", "staff-dashboard-case"):
            st.session_state.pop(key, None)
        st.session_state["staff-dashboard-last-scope"] = current_scope
    rows = list_staff_cases(user, filters=location_filter) if location_filter else []
    summary = {
        "new": sum(row["status"] == "new" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
        "overdue": sum(row["sla"]["overdue"] for row in rows),
        "awaiting_clarification": sum(row["status"] == "awaiting clarification" for row in rows),
        "resolved": sum(row["status"] == "resolved" for row in rows),
    }
    hotspots = get_ward_hotspots(user) if feature_enabled("hotspots") else []
    if active_assignment and is_city_wide and not selected_location_name:
        hotspots = []
    elif active_assignment and is_city_wide:
        hotspots = [item for item in hotspots if item["location"] == selected_location_name]
    elif active_assignment and not is_city_wide:
        hotspots = [item for item in hotspots if item["location"] == active_area_name]
    issues = sorted({row.get("issue") or "Uncategorized" for row in rows})
    if user.get("role") == "operator":
        access_label = f"Department: {user.get('department') or 'not assigned'}"
    elif user.get("role") == "auditor":
        access_label = "Read-only audit scope · selected service area"
    else:
        access_label = "Review scope · selected service area"
    st.caption(f"Access scope: {access_label}")
    st.caption(f"Showing only authorized complaints for {active_area_name}.")
    st.caption("SLA values are configurable prototype targets, not official service guarantees.")

    st.subheader("Workload overview")
    metric_cols = st.columns(5)
    metrics = [("New", "new"), ("Pending", "pending"), ("Overdue", "overdue"),
               ("Awaiting clarification", "awaiting_clarification"), ("Resolved", "resolved")]
    for column, (label, key) in zip(metric_cols, metrics):
        column.metric(label, summary[key])

    if feature_enabled("hotspots"):
        with st.expander("Area activity · exploratory hotspot signals", expanded=False):
            st.caption("Counts compare the latest 7 days with the preceding 7 days. Synthetic data and small samples do not establish a validated trend.")
            if not hotspots:
                st.write("No hotspot summary is available for this service area and your department scope.")
            else:
                for index, item in enumerate(hotspots):
                    col, metric, action = st.columns([2, 1.2, 1.5], vertical_alignment="center")
                    col.markdown(f"**{item['location']} · {item['issue']}**")
                    metric.metric("Pending reports", item["count"],
                                  delta=f"{item['observed_count']} recent · {item['baseline_count']} previous")
                    action.caption(f"{item['data_label']} · {item['sample_status'] or 'sample count shown'}")
                    if action.button("Filter by issue", key=f"hotspot-filter-{index}"):
                        if item["issue"] in issues:
                            st.session_state["staff-inbox-issue"] = item["issue"]
                        st.rerun()
            st.page_link("pages/13_Hotspots.py", label="Open hotspot analysis", icon="📈")

    st.subheader("Officer inbox")
    st.caption(f"{len(rows)} complaints in {active_area_name}, after role and department checks.")
    if not rows:
        if is_city_wide and not selected_location_name:
            st.info("Choose a ward / area above to load only the complaints for that location.")
            return
        st.info(f"No complaints are currently available in {active_area_name}.")
        return

    statuses = sorted({row["status"] for row in rows})
    issue_default = st.session_state.get("staff-inbox-issue", "All categories")
    if issue_default not in issues and issue_default != "All categories":
        issue_default = "All categories"
        st.session_state["staff-inbox-issue"] = issue_default
    status_default = st.session_state.get("staff-inbox-status", "All statuses")
    if status_default not in statuses and status_default != "All statuses":
        st.session_state["staff-inbox-status"] = "All statuses"
    with st.expander("Search and filters", expanded=False):
        search_col, status_col, issue_col, sort_col = st.columns([2, 1.2, 1.5, 1.5])
        search = search_col.text_input("Search case, text, or location", key="staff-inbox-search")
        status = status_col.selectbox("Status", ["All statuses", *statuses], key="staff-inbox-status")
        issue = issue_col.selectbox("Category", ["All categories", *issues],
                                    index=(["All categories", *issues].index(issue_default)),
                                    key="staff-inbox-issue")
        sort_by = sort_col.selectbox("Sort by", ["Newest first", "Oldest first", "SLA deadline", "Overdue first"],
                                     key="staff-inbox-sort")
        flag_col, overlap_col, overdue_col, date_col = st.columns([1.1, 1.5, 1.1, 2.1])
        safety_only = flag_col.checkbox("Safety flag only", key="staff-inbox-safety")
        overlap_only = overlap_col.checkbox("Ownership conflict only", key="staff-inbox-overlap")
        overdue_only = overdue_col.checkbox("Overdue only", key="staff-inbox-overdue")
        date_range = date_col.date_input("Submitted date range", value=(date.today() - timedelta(days=90), date.today()),
                                         key="staff-inbox-dates")

    visible = rows
    if search:
        term = normalize_location(search) or ""
        visible = [row for row in visible if term in (normalize_location(" ".join(
                   str(row.get(key) or "") for key in
                   ("case_id", "original_text", "issue", "location", "location_normalized", "area_name"))) or "")]
    if status != "All statuses":
        visible = [row for row in visible if row["status"] == status]
    if issue != "All categories":
        visible = [row for row in visible if (row.get("issue") or "Uncategorized") == issue]
    if safety_only:
        visible = [row for row in visible if row["safety"]["flagged"]]
    if overlap_only:
        visible = [row for row in visible if row["ownership_conflicts"]]
    if overdue_only:
        visible = [row for row in visible if row["sla"]["overdue"]]
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        lower, upper = date_range
        visible = [row for row in visible if row.get("created_at") and _parse_time(row["created_at"])
                   and lower <= _parse_time(row["created_at"]).date() <= upper]
    if sort_by == "Oldest first":
        visible.sort(key=lambda row: _parse_time(row.get("created_at")) or datetime.max.replace(tzinfo=timezone.utc))
    elif sort_by == "SLA deadline":
        visible.sort(key=lambda row: _parse_time(row["sla"].get("deadline")) or datetime.max.replace(tzinfo=timezone.utc))
    elif sort_by == "Overdue first":
        visible.sort(key=lambda row: (not row["sla"]["overdue"],
                                      _parse_time(row["sla"].get("deadline")) or datetime.max.replace(tzinfo=timezone.utc)))

    display = [{"Case ID": row["case_id"], "Submitted": _date_label(row.get("created_at")),
                "Issue": row.get("issue") or "Uncategorized",
                "Ward / area": row.get("area_name") or row.get("location") or "Area not recorded",
                "Suggested department": row.get("suggested_department") or "Human review needed",
                "Current department": row.get("current_department") or "Not assigned",
                "Status": row["status"],
                "Split review": (row.get("split_status") or "—").replace("_", " "),
                "SLA": row["sla"]["label"],
                "Safety": "Review" if row["safety"]["flagged"] else "—",
                "Ownership": "Check" if row["ownership_conflicts"] else "—"}
               for row in visible]
    if display:
        st.caption(f"Showing {len(display)} of {len(rows)} complaints for the selected location.")
        with st.container(border=True, key="staff-inbox-results"):
            st.dataframe(pd.DataFrame(display), hide_index=True, width="stretch")
        ids = [row["id"] for row in visible]
        case_key = "staff-dashboard-case"
        if st.session_state.get(case_key) not in ids:
            st.session_state[case_key] = ids[0]
        case_id = st.selectbox("Select a complaint to review", ids,
                               format_func=lambda selected: _case_label(next(r for r in visible if r["id"] == selected)),
                               key=case_key)
        detail = get_staff_case_detail(user, case_id)
        complaint = detail["complaint"]
        st.divider()
        st.subheader(f"Selected complaint · {complaint['case_id']}")
        if verify_permission(user.get("role"), "can_review"):
            with st.expander("Correct extracted fields"):
                st.caption("Changes are recorded as an append-only ledger event. Original complaint text and routing suggestions are not changed. Location text stays within the approved service-area assignment.")
                issue_values = ["unknown", *(name for name, _ in ISSUE_CATEGORIES)]
                current_issue = complaint.get("issue") or "unknown"
                issue_index = issue_values.index(current_issue) if current_issue in issue_values else 0
                corrected_issue = st.selectbox("Issue category", issue_values, index=issue_index,
                                               key=f"extract-issue-{complaint['id']}")
                corrected_location = st.text_input("Street or landmark", value=complaint.get("location") or "unknown",
                                                    max_chars=250, key=f"extract-location-{complaint['id']}")
                action_values = ["unspecified", "repair", "clean", "clear", "inspect"]
                current_action = complaint.get("requested_action") or "unspecified"
                action_index = action_values.index(current_action) if current_action in action_values else 0
                corrected_action = st.selectbox("Requested action", action_values, index=action_index,
                                                key=f"extract-action-{complaint['id']}")
                correction_code = st.selectbox("Correction reason code", REASON_CODES,
                                               key=f"extract-reason-code-{complaint['id']}")
                correction_reason = st.text_area("Why is this extraction incorrect? (required)", max_chars=1000,
                                                 key=f"extract-reason-{complaint['id']}")
                if st.button("Save extraction correction", key=f"extract-save-{complaint['id']}"):
                    try:
                        correct_intake_fields(user, complaint["id"], {
                            "issue": corrected_issue,
                            "location": corrected_location,
                            "requested_action": corrected_action,
                        }, correction_reason, correction_code)
                        st.success("Extracted fields and correction history saved.")
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
        st.markdown("### Complaint details")
        detail_left, detail_right = st.columns([1.4, 1], gap="large")
        with detail_left:
            st.markdown("**Original complaint**")
            st.write(mask_text(complaint["original_text"]))
            if complaint.get("normalized_text") and complaint["normalized_text"] != complaint["original_text"]:
                st.markdown("**Normalized text**")
                st.write(mask_text(complaint["normalized_text"]))
            st.caption(f"Language: {complaint['language']} · Channel: {complaint['channel']} · "
                       f"Submitted: {_date_label(complaint['created_at'])}")
            st.markdown(f"**Extracted issue:** {complaint.get('issue') or 'Unknown'} · "
                        f"**Requested action:** {complaint.get('requested_action') or 'Unspecified'}")
            st.caption(f"Missing information: {complaint.get('missing_fields') or 'None recorded'}")
            st.caption("Speech input, when used, must be reviewed and confirmed before processing. Audio is not retained; "
                       "the saved original text remains the source record.")
        with detail_right:
            st.metric("Current state", detail["status"].title(), delta=detail["sla"]["label"])
            st.write(f"**Suggested department:** {detail['suggested_department'] or 'No selected suggestion'}")
            st.write(f"**Current department:** {detail['current_department'] or 'Not assigned'}")
            if detail["uncertain"]:
                if detail.get("split_status") in {"proposed", "edited"}:
                    st.warning("Compound complaint · no department is assigned to the parent. Staff must confirm or edit the proposed sub-issues.")
                elif detail.get("split_status") == "confirmed":
                    st.info("The parent remains unassigned as a whole. Review the confirmed sub-issues below for their separate routes.")
                else:
                    st.warning("No usable routing suggestion is available. A human decision is required.")
        st.markdown("### Evidence and review signals")
        if detail["routing_history"]:
            st.markdown("**Routing evidence and uncertainty**")
            for item in detail["routing_history"]:
                st.caption(f"{item['method']} · {item['created_at']} · "
                           f"{'Manual review required' if item['abstained'] else 'Recommendation only'}")
                st.json({"suggestions": item["top3"], "evidence": item["evidence"]})
        if detail["safety"]["flagged"]:
            st.error(f"Safety flag · human attention required · rubric v{detail['safety'].get('rubric_version', 1)} · "
                     f"{detail['safety']['rubric_status']}")
            for flag in detail["safety"]["flags"]:
                st.write(f"{flag['label']} · matched: {', '.join(flag['matched_text'])}")
        if detail["ownership_conflicts"]:
            st.warning("Possible ownership overlap · manual review required; this flag does not decide responsibility or assign a department.")
            for conflict in detail["ownership_conflicts"]:
                verification = str(conflict.get("verification", "UNKNOWN")).strip().upper()
                verification_label = ("unverified rule" if verification in {"ASSUMPTION", "UNKNOWN", "UNVERIFIED"}
                                      else verification)
                st.write(f"{conflict['owner']} ↔ {conflict['overlap_owner']} · {conflict['issue_type']} · "
                         f"Source: {conflict['source']} · {verification_label}")
        if detail["bounce_warning"] or detail["repeat_reassignment"]:
            warning = detail["bounce_warning"] or detail["repeat_reassignment"]
            st.warning(f"Repeated department reassignment detected for review: {warning}")
        if detail["open_challenges"]:
            st.info(f"Open resident/staff review requests: {len(detail['open_challenges'])}")
            for request in detail["open_challenges"]:
                st.write(f"{request['raised_by'].title()} · {request['created_at']} · {request['reason']}")
        if detail["duplicates"]:
            st.markdown("**Possible related incidents · review only**")
            st.dataframe([{"Case ID": item["case_id"], "Score": item["score"],
                           "Decision": item["decision"], "Outcome": item["candidate_outcome"]}
                          for item in detail["duplicates"]], hide_index=True, width="stretch")
        if detail["confirmed_subissues"]:
            st.markdown("**Human-confirmed sub-issues**")
            st.dataframe([{"Case ID": item["case_id"], "Issue": item["issue"],
                           "Location": item["location"], "Text": mask_text(item["original_text"])}
                          for item in detail["confirmed_subissues"]], hide_index=True, width="stretch")
        if detail["attachments"]:
            from .attachment import read_attachment
            st.markdown("**Photo attachments · stored only, not analysed**")
            for attachment in detail["attachments"]:
                try:
                    st.image(read_attachment(attachment["id"], user["role"], actor_user=detail["actor"]),
                             caption=attachment["original_name"])
                except (PermissionError, ValueError) as exc:
                    st.caption(f"Attachment unavailable: {exc}")
        with st.expander("Immutable case timeline", expanded=False):
            for item in detail["timeline"]:
                actor = item.get("actor") or "system"
                reason = item.get("reason") or ""
                reason_code = item.get("reason_code")
                suffix = f" · {reason_code}" if reason_code else ""
                detail_text = ""
                raw_details = item.get("details")
                if raw_details:
                    try:
                        event_details = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
                    except (TypeError, ValueError):
                        event_details = {}
                    old_status, new_status = event_details.get("prior_status"), event_details.get("new_status")
                    old_department = event_details.get("previous_department")
                    new_department = event_details.get("new_department")
                    if old_status or new_status:
                        detail_text += f" · status {old_status or '—'} → {new_status or '—'}"
                    if old_department or new_department:
                        detail_text += f" · department {old_department or '—'} → {new_department or '—'}"
                st.write(f"{_date_label(item.get('created_at'))} · **{item['event_type'].replace('_', ' ').title()}** · "
                         f"{actor}{suffix}{detail_text}{(' — ' + reason) if reason else ''}")
    else:
        st.info("No complaints match these filters.")

    if verify_permission(user.get("role"), "can_review") or user.get("role") == "operator":
        if visible:
            _render_action_form(user, visible, case_id)
    else:
        st.caption("Auditor access is read-only.")


def _render_action_form(user, visible, case_id):
    selected = next((row for row in visible if row["id"] == case_id), None)
    if not selected or selected["status"] == "resolved":
        return
    st.subheader("Record an officer action")
    st.caption("Actions are recorded in the append-only ledger. Dispatch is recorded locally; no field unit is contacted.")
    with st.form("staff-action-form"):
        action = st.selectbox("Action", ["dispatch", "clarify", "resolve", "transfer"],
                              format_func=lambda value: {
                                  "dispatch": "Dispatch Field Unit (record only)",
                                  "clarify": "Request Clarification",
                                  "resolve": "Mark Resolved",
                                  "transfer": "Transfer to Another Department",
                              }[value])
        note = st.text_area("Action note / reason", max_chars=1000)
        target_department = None
        reason_code = None
        if action == "transfer":
            target_department = st.selectbox("Receiving department", [
                dept for dept in ("Roads Dept", "Drainage Dept", "Sanitation Dept", "Electricity Dept",
                                  "Water Supply Dept", "Grievance Cell")
                if dept != selected["current_department"]])
            reason_code = st.selectbox("Transfer reason code", REASON_CODES)
        elif action == "resolve":
            reason_code = st.selectbox("Resolution reason code (optional)", ["", *REASON_CODES]) or None
        confirmed = st.form_submit_button(f"Confirm {action.replace('_', ' ')} for {selected['case_id']}",
                                          type="primary")
    if confirmed:
        try:
            result = perform_staff_action(user, case_id, action, note=note,
                                          reason_code=reason_code,
                                          target_department=target_department)
            st.success(f"Action recorded: {result.get('status', action)}")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
