import json
from datetime import date, timedelta, datetime, time, timezone

import pandas as pd
import streamlit as st

from core.config import feature_enabled
from core.db import get_conn
from core.hotspots import detect_hotspot_signals
from core.staff_dashboard import get_staff_case_detail, list_staff_cases
from core.ui import apply_demo_style, render_hero, require_permission

user = require_permission("can_view_hotspots")
if not feature_enabled("hotspots"):
    st.info("Hotspot review signals are disabled by configuration.")
    st.stop()
apply_demo_style()
render_hero("Staff insights", "Potential hotspots", "Review unusual complaint concentration within your authorized service scope.")

all_cases = list_staff_cases(user)
area_options = sorted({case.get("area_name") or case.get("location") or "Unknown area" for case in all_cases},
                      key=str.casefold)
category_options = sorted({case.get("issue") or "Uncategorized" for case in all_cases}, key=str.casefold)
controls = st.columns([1.2, 1, 1, 1])
window = controls[0].selectbox("Time window", [7, 14, 30, 90], format_func=lambda value: f"Last {value} days")
area_filter = controls[1].selectbox("Ward / area", ["All authorized areas", *area_options])
category_filter = controls[2].selectbox("Issue category", ["All categories", *category_options])
end_date = controls[3].date_input("Period ending", value=date.today())
start_date = end_date - timedelta(days=window - 1)
st.caption(f"Current range: {start_date.isoformat()} to {end_date.isoformat()} · baseline: preceding {window} days")

try:
    report = detect_hotspot_signals(
        user, window_days=window,
        as_of=datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc),
        area_filter=area_filter, category_filter=category_filter,
    )
except (ValueError, PermissionError) as exc:
    st.error(str(exc))
    st.stop()

st.warning(report["data_label"])
summary = st.columns(3)
summary[0].metric("Complaints in period", report["current_count"])
summary[1].metric("Potential hotspots", report["areas_flagged"])
summary[2].metric("Comparison window", f"{report['window_days']} days")
st.caption(f"Rule: {report['rule']}. A signal is a prompt for human review, not proof of cause or negligence.")

if report["status"] == "insufficient_data":
    st.info("Insufficient data for a period comparison in this authorized scope.")
elif not report["hotspots"]:
    st.success("No area/category group met the configured review-signal threshold for this period.")
else:
    table = [{
        "Area / ward": item["area"], "Category": item["category"],
        "Current count": item["current_count"], "Baseline count": item["baseline_count"],
        "Change": f"+{item['increase']}" if item["increase"] >= 0 else str(item["increase"]),
        "Why flagged": item["rule"], "Data": item["data_label"],
    } for item in report["hotspots"]]
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption(f"Current: {report['current_start']} – {report['current_end']} · "
               f"Baseline: {report['comparison_start']} – {report['comparison_end']}")
    cases_by_id = {case["id"]: case for case in all_cases}
    for index, signal in enumerate(report["hotspots"]):
        with st.expander(f"{signal['area']} · {signal['category']} · {signal['current_count']} reports"):
            st.caption(signal["interpretation"])
            related = [cases_by_id[case_id] for case_id in signal["complaint_ids"] if case_id in cases_by_id]
            if not related:
                st.info("No current-period complaint records remain in this authorized filter.")
                continue

            # Proactive municipal field inspection action
            st.markdown("#### Proactive Field Operations")
            disp_col1, disp_col2 = st.columns([2, 1])
            insp_note = disp_col1.text_input(
                f"Inspection directive for {signal['area']}",
                value=f"Proactive field survey for {signal['category']} cluster in {signal['area']}.",
                key=f"directive-{index}"
            )
            if disp_col2.button(f"Dispatch Field Inspection Team", key=f"dispatch-team-{index}", type="primary"):
                conn = get_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    actor_email = user.get("email", "staff")
                    for case_id in signal["complaint_ids"]:
                        conn.execute(
                            "INSERT INTO ledger (complaint_id, event_type, actor, reason, details) "
                            "VALUES (?, 'hotspot_inspection_dispatched', ?, ?, ?)",
                            (case_id, actor_email, insp_note,
                             json.dumps({"area": signal["area"], "category": signal["category"],
                                         "cluster_count": signal["current_count"]}))
                        )
                    conn.commit()
                    st.success(f"Inspection team dispatched and recorded in ledger for {len(signal['complaint_ids'])} cases.")
                except Exception as exc:
                    conn.rollback()
                    st.error(f"Dispatch recording failed: {exc}")
                finally:
                    conn.close()

            st.divider()
            labels = {f"{case['case_id']} · {case.get('created_at')} · {case.get('location') or 'Area not recorded'}": case
                      for case in related}
            selected_label = st.selectbox("Related complaints in this cluster", list(labels), key=f"hotspot-case-{index}")
            if st.button("Open case details", key=f"hotspot-open-{index}"):
                try:
                    st.session_state[f"hotspot-detail-{index}"] = get_staff_case_detail(user, labels[selected_label]["id"])
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
            detail = st.session_state.get(f"hotspot-detail-{index}")
            if detail:
                st.write(detail["complaint"]["original_text"])
                st.caption(f"Suggested department: {detail['suggested_department'] or 'Manual review'} · "
                           f"Status: {detail['status']}")