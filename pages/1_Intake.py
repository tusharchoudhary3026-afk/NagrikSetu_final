import uuid
import streamlit as st

from core import intake, ownership, router_baseline, splitter
from core.attachment import save_attachment
from core.config import SIM_LABEL, feature_enabled
from core.db import init_db, insert_complaint, insert_suggestion
from core.ledger import log_event
from core.safety import flag_safety
from core.security import attachment_encryption_status
from core.suggestions import selected_department
from core.ui import apply_demo_style, render_hero, require_permission
from core.geo import staff_area_scope
from core.sarvam_speech import transcribe_audio

init_db()
user = require_permission("can_intake")
if user.get("role") not in {"government", "operator", "reviewer"}:
    st.error("Use the resident portal to submit a complaint.")
    st.stop()
assigned_areas = staff_area_scope(user["id"])
apply_demo_style()
render_hero("Complaint intake", "Complain here", "Capture the original report, inspect suggestions, and save it for a person to review.")
st.warning(SIM_LABEL)

# Sarvam AI Voice Intake Section
if feature_enabled("voice"):
    st.markdown("### 🎙️ Voice Intake (Powered by Sarvam AI)")
    st.caption("Record your grievance in Hindi, English, or Hinglish. Sarvam AI will transcribe it accurately.")
    
    audio_value = st.audio_input("Record your complaint voice note", key="sarvam_audio_recorder")
    
    if audio_value is not None:
        audio_bytes = audio_value.getvalue()
        if audio_bytes != st.session_state.get("last_audio_bytes"):
            with st.spinner("Transcribing audio via Sarvam AI..."):
                try:
                    transcript = transcribe_audio(audio_bytes, audio_value.name)
                    if transcript:
                        st.session_state["intake_text"] = transcript
                        st.session_state["last_audio_bytes"] = audio_bytes
                        st.success("Audio transcribed successfully! You can review or edit the text below.")
                        st.rerun()
                except Exception as exc:
                    st.error(str(exc))
else:
    st.caption("Voice intake is disabled by configuration.")

text = st.text_area("Complaint (Hindi / English / Hinglish) · edit the transcript before processing", height=120,
                    max_chars=intake.MAX_COMPLAINT_CHARS,
                    key="intake_text")

channel = st.selectbox("Channel", ["portal", "phone", "social", "in-person"])
loc_hint = st.text_input("Street or landmark (optional, staff-assisted)")
if assigned_areas:
    area_by_key = {(row["state_code"], row["city_code"], row["area_code"]): row for row in assigned_areas}
    selected_area = st.selectbox("Verified service area", list(area_by_key),
                                 format_func=lambda key: " · ".join(str(area_by_key[key][part])
                                                                     for part in ("state_name", "city_name", "area_name")))
else:
    selected_area = None
    st.warning("No verified area is assigned. Staff-assisted intake stays unavailable until access is approved.")

if st.button("Process complaint", type="primary"):
    try:
        if not assigned_areas:
            raise ValueError("Select a service area before using staff-assisted intake.")
        if not text or not text.strip():
            raise ValueError("Complaint text cannot be empty.")
        rec = intake.process(text, channel, loc_hint)
        area = area_by_key[selected_area]
        rec.update({key: area[key] for key in ("state_code", "state_name", "city_code", "city_name",
                                               "area_code", "area_name")})
        rec["location"] = loc_hint.strip() or area["area_name"]
        st.session_state["rec"] = rec
        st.session_state["split"] = splitter.propose_split(text)
        st.session_state["route"] = router_baseline.route(text)
        st.session_state["safety"] = flag_safety(text)
    except ValueError as exc:
        st.error(str(exc))

if "rec" in st.session_state:
    rec, sp, rt = st.session_state["rec"], st.session_state["split"], st.session_state["route"]
    st.subheader("Extracted fields")
    st.write({"language": rec["language"], "location": rec["location"], "issue": rec["issue"],
              "requested action": rec["requested_action"], "channel": rec["channel"]})
    if rec.get("issue_evidence"):
        st.caption("Matched issue words: " + ", ".join(rec["issue_evidence"]))
    st.caption("The original complaint is preserved separately and never replaced by normalized text.")
    question = intake.clarification_question(rec)
    if question:
        st.info(f"Clarification question: {question}")

    safety = st.session_state.get("safety", {"flagged": False})
    if safety["flagged"]:
        st.error(f"Human attention flag · {safety['rubric_status']}: " + "; ".join(x["label"] for x in safety["flags"]))
        st.caption(safety["message"])

    st.subheader("Routing suggestions (keyword baseline)")
    if rt["abstained"]:
        st.warning("No supported route found. This complaint remains in manual review.")
    for suggestion in rt["top3"]:
        st.write(f"{suggestion['dept']} · score {suggestion['score']} · evidence: {rt['evidence'].get(suggestion['dept'], [])}")
    for overlap in ownership.check_suggestions([x["dept"] for x in rt["top3"]], text):
        st.warning(f"Possible ownership overlap: {overlap['owner']} / {overlap['overlap_owner']} · "
                   f"{overlap['verification']} · {overlap['source']}")

    st.subheader("Possible sub-issues")
    st.write(sp["note"])
    if sp["is_compound"]:
        for part in sp["parts"]:
            st.write(f"- {part['text']} → {[x['dept'] for x in part['suggestion']['top3'][:1]]}")
    confirm_split = False
    if sp["is_compound"] and user["role"] == "government":
        confirm_split = st.checkbox("I reviewed and confirm these proposed sub-issues")
    elif sp["is_compound"]:
        st.caption("A reviewer must confirm the proposed split. The complaint will be saved intact for now.")
    
    photos_enabled = feature_enabled("photos")
    photo_status = attachment_encryption_status()
    if not photos_enabled:
        st.caption("Photo evidence is disabled by configuration.")
        photo = None
    else:
        if photo_status["configured"]:
            st.caption("Photo evidence storage: AES-256-GCM encryption at rest. Images are not analyzed.")
        else:
            st.warning("Secure photo upload is unavailable until encrypted photo storage is configured.")
        photo = st.file_uploader("Optional photo evidence (JPEG, PNG or WEBP, max 5 MB; no image analysis)",
                                 type=["jpg", "jpeg", "png", "webp"], disabled=not photo_status["configured"])

    if st.button("Save to review queue"):
        if photo is not None and not photo_status["configured"]:
            st.error("Photo not saved: secure photo storage is not configured.")
            st.stop()
        rec["case_id"] = f"CASE-{uuid.uuid4().hex.upper()}"
        try:
            cid = insert_complaint(rec)
            insert_suggestion(cid, "baseline", rt["top3"], rt["evidence"], rt["confidence"], rt["abstained"])
            top = selected_department(rt)
            log_event(cid, "suggested", "system", suggested_dept=top,
                      details={"abstained": rt["abstained"], "confidence": rt["confidence"],
                               "safety_flags": safety.get("flags", []),
                               "extracted_issue_evidence": rec.get("issue_evidence", [])}, actor_user=user)
            if photo is not None:
                save_attachment(cid, photo, user["role"], actor_user=user)
            if confirm_split:
                child_ids = splitter.confirm_split(cid, rec["case_id"], sp["parts"], user["role"],
                                                   actor_email=user.get("email"), actor_user=user)
                st.info(f"Human-confirmed split saved as {len(child_ids)} linked sub-issues.")
            st.success(f"Saved as {rec['case_id']}")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))