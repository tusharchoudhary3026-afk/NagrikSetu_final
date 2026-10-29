import json
import streamlit as st
from core.db import init_db, fetch_all
from core.ledger import history
from core.ledger_integrity import verify_ledger_integrity
from core.attachment import get_attachments, read_attachment
from core.analytics import get_case_reassignment_timeline
from core.audit import generate_audit_export
from core import ownership
from core.config import SIM_LABEL
from core.privacy import mask_text
from core.ui import apply_demo_style, render_hero
from core.ui import require_permission
from core.auth import verify_permission
from core.geo import is_geography_admin, scoped_complaints

init_db()
user = require_permission("can_audit")
apply_demo_style()
render_hero("Audit", "Case replay", "One case from first suggestion to latest decision, in order.")
st.warning(SIM_LABEL)

st.subheader("Ledger integrity check")
st.caption("Checks the SHA-256 event chain against the stored ledger. This detects content changes; it does not make the database tamper-proof.")
if st.button("Verify ledger integrity", key="verify-ledger-integrity"):
    try:
        integrity = verify_ledger_integrity(user)
        if integrity["valid"]:
            st.success(f"Integrity check passed: {integrity['checked']}/{integrity['total']} events verified.")
        else:
            event = f" at ledger event {integrity['first_invalid_id']}" if integrity["first_invalid_id"] else ""
            st.error(f"Integrity check failed{event}: {integrity['problem']} "
                     f"({integrity['checked']}/{integrity['total']} events checked).")
    except PermissionError as exc:
        st.error(str(exc))

rows = [{key: row[key] for key in ("id", "case_id", "parent_id", "original_text", "is_active")}
        for row in scoped_complaints(user, include_unassigned=is_geography_admin(user.get("email", "")))]
if not rows:
    st.info("No complaints yet.")
    st.stop()
pick = st.selectbox("Case", rows, format_func=lambda r: f"{r['case_id']} | "
                    f"{'merged-back sub-issue' if not r['is_active'] else 'active'} | {mask_text(r['original_text'])[:50]}")
st.write("**Original text:**", mask_text(pick["original_text"]))

st.subheader("Photo evidence")
st.caption("Photo attachments are stored securely and are not analysed automatically.")
if not verify_permission(user.get("role"), "can_view_attachments"):
    st.caption("Photo attachments are hidden for this role.")
else:
    for photo in get_attachments(pick["id"], user["role"], actor_user=user):
        try:
            st.image(read_attachment(photo["id"], user["role"], actor_user=user),
                     caption=f"photo attachment: stored only, not analysed · {photo['original_name']}")
        except ValueError as exc:
            st.warning(str(exc))

ICON = {"suggested": "🤖", "split_proposed": "✂️", "split_edited": "✏️",
        "accept": "✅", "edit": "✏️", "clarify": "❓", "escalate": "⬆️",
        "transfer": "↔️", "challenge": "⚖️", "challenge_resolved": "🔒", "split_confirmed": "✂️",
        "split_merged_back": "🔗", "duplicate_linked": "🔗", "duplicate_confirmed": "🔗",
        "duplicate_rejected": "🚫", "duplicate_unlinked": "↩️",
        "correction_verified": "🏷️"}
h = history(pick["id"], actor_user=user)
timeline = get_case_reassignment_timeline(pick["id"], actor_user=user)
st.subheader("Timeline (append-only ledger)")
if not timeline:
    st.caption("No ledger events for this case yet.")
for e in timeline:
    stamp = next((str(e[k]) for k in e if any(s in k.lower() for s in ("time", "created", "_at", "ts"))), "")
    with st.container(border=True):
        st.markdown(f"{ICON.get(e['event_type'], '•')} **{e['event_type']}** · {e['actor']} · "
                    f"{e['case_id']} · {stamp}")
        bits = []
        if e.get("suggested_dept"): bits.append(f"suggested: {e['suggested_dept']}")
        if e.get("final_dept"): bits.append(f"final: {e['final_dept']}")
        if e.get("reason_code"): bits.append(f"code: {e['reason_code']}")
        if bits: st.caption(" · ".join(bits))
        if e.get("reason"): st.write(f"Reason: {mask_text(e['reason'])}")
        if e.get("details"):
            try: st.caption(json.dumps(json.loads(e["details"])))
            except Exception: st.caption(str(e["details"]))

st.subheader("Pattern flags (no blame)")
b, r = ownership.detect_bounce(h), ownership.repeat_reassignment(h)
if b: st.warning(f"A → B → A pattern: {b['pattern']}")
if r: st.warning(f"Repeat reassignment: {r['moves']} owner changes ({r['path']})")
if not (b or r): st.caption("No transfer pattern detected for this case.")

ch = fetch_all("SELECT * FROM challenges WHERE complaint_id IN "
               "(SELECT id FROM complaints WHERE case_id=?) ORDER BY id", (pick["case_id"],))
if ch:
    st.subheader("Challenges")
    for c in ch:
        st.write(f"#{c['id']} · {c['raised_by']} · {c['status']}: {mask_text(c['reason'])}")

st.download_button("Download masked ledger audit report", generate_audit_export(
    user["role"], actor_user=user), file_name="nagriksetu-audit.json",
    mime="application/json")
