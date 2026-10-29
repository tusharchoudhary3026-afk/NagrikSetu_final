import pandas as pd
import streamlit as st
from core.db import init_db, fetch_all
from core.config import SIM_LABEL
from core.privacy import mask_text
from core.ui import apply_demo_style, render_hero
from core.ui import require_permission
from core.geo import is_geography_admin, scoped_complaints

init_db()
require_permission("can_audit")
user = st.session_state["authenticated_user"]
apply_demo_style()
render_hero("Authorized staff view", "Complaint inbox",
            "Cases within your approved service areas, with channel, language, and human-review status.")
st.warning(SIM_LABEL)

rows = scoped_complaints(user, include_unassigned=is_geography_admin(user.get("email", "")))
if not rows:
    st.info("No complaints yet. Use 'Complain here' first.")
    st.stop()

ids = [row["id"] for row in rows]
details = {}
if ids:
    placeholders = ",".join("?" for _ in ids)
    details = {row["complaint_id"]: row for row in fetch_all(
        "SELECT c.id AS complaint_id,"
        "(SELECT abstained FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1) AS abstained,"
        "(SELECT COUNT(*) FROM challenges h WHERE h.complaint_id=c.id AND h.status='open') AS open_challenges,"
        "(SELECT event_type FROM ledger l WHERE l.complaint_id=c.id AND l.event_type IN "
        "('accept','edit','clarify','escalate','transfer') ORDER BY l.id DESC LIMIT 1) AS last_decision "
        f"FROM complaints c WHERE c.id IN ({placeholders})", ids)}
for row in rows:
    row.update(details.get(row["id"], {}))
df = pd.DataFrame(rows)
df["status"] = df.apply(
    lambda row: "review requested" if row["open_challenges"] else
        (row["last_decision"] if pd.notna(row["last_decision"]) else "needs review"),
    axis=1,
)
df["kind"] = df["parent_id"].apply(lambda p: "sub-issue" if pd.notna(p) else "case")
df["uncertain"] = df["abstained"].apply(lambda a: "yes" if a == 1 else "no")
df["text"] = df["original_text"].apply(lambda t: mask_text(t)[:90])
df["created_day"] = pd.to_datetime(df["created_at"], errors="coerce").dt.date

c1, c2, c3 = st.columns(3)
area_options = sorted(df["area_name"].dropna().unique()) if "area_name" in df else []
area = st.selectbox("Assigned area", ["All assigned areas"] + area_options)
lang = c1.multiselect("Language", sorted(df["language"].unique()))
chan = c2.multiselect("Channel", sorted(df["channel"].dropna().unique()))
stat = c3.multiselect("Status", sorted(df["status"].unique()))
date_span = st.date_input("Submitted between", value=(df["created_day"].min(), df["created_day"].max()))
review_filter = st.selectbox("Review state", ["All", "Needs review", "Uncertain / abstained"])
view = df
if lang: view = view[view["language"].isin(lang)]
if chan: view = view[view["channel"].isin(chan)]
if stat: view = view[view["status"].isin(stat)]
if area != "All assigned areas" and "area_name" in view: view = view[view["area_name"] == area]
if isinstance(date_span, (tuple, list)) and len(date_span) == 2:
    view = view[view["created_day"].between(date_span[0], date_span[1])]
if review_filter == "Needs review":
    view = view[view["status"].isin(["needs review", "review requested"])]
elif review_filter == "Uncertain / abstained":
    view = view[view["uncertain"] == "yes"]

m1, m2, m3 = st.columns(3)
m1.metric("Shown", f"{len(view)} of {len(df)}")
m2.metric("Needs review", int((view["status"] == "needs review").sum()))
m3.metric("Open challenges", int(view["open_challenges"].sum()))

st.dataframe(view[["case_id", "state_name", "city_name", "area_name", "kind", "channel", "language", "location", "issue",
                   "uncertain", "status", "open_challenges", "text"]],
             width="stretch", hide_index=True)
st.caption("Phone numbers and emails are masked in this view. Nothing here is auto-closed or auto-assigned.")
