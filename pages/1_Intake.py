import hashlib
import logging
import uuid
import streamlit as st

from core import intake, ownership, router_baseline, splitter
from core.attachment import save_attachment
from core.config import SIM_LABEL, feature_enabled
from core.db import init_db, insert_public_submission
from core.safety import assess_safety
from core.explanation import generate_grounded_explanation
from core.suggestions import selected_department
from core.security import attachment_encryption_status
from core.ui import apply_demo_style, render_hero, require_permission
from core.geo import staff_area_scope
from core.sarvam_speech import sarvam_configured, transcribe_audio

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
    st.caption("Record your grievance in Hindi, English, or Hinglish. Review the transcript before processing.")
    speech_language = st.selectbox(
        "Spoken language", ["Auto-detect", "Hindi", "English", "Hinglish"],
        key="staff-speech-language",
    )
    language_code = {"Hindi": "hi-IN", "English": "en-IN"}.get(speech_language, "unknown")
    voice_ready = sarvam_configured()
    if not voice_ready:
        st.warning("Voice transcription is not configured. Add a valid SARVAM_API_KEY to .env or .streamlit/secrets.toml, then refresh the page.")
    
    audio_value = st.audio_input("Record your complaint voice note", sample_rate=16000,
                                 key="sarvam_audio_recorder", disabled=not voice_ready)
    
    if audio_value is not None and voice_ready:
        audio_bytes = audio_value.getvalue()
        audio_digest = hashlib.sha256(language_code.encode("ascii") + b"\0" + audio_bytes).hexdigest()
        digest_key = "last_audio_digest"
        error_key = "audio_transcription_error"
        if audio_digest != st.session_state.get(digest_key):
            st.session_state[digest_key] = audio_digest
            st.session_state.pop(error_key, None)
            with st.spinner("Transcribing audio via Sarvam AI..."):
                try:
                    transcript = transcribe_audio(
                        audio_bytes, audio_value.name, language_code=language_code)
                    if transcript:
                        st.session_state["intake_text"] = transcript
                        st.session_state.pop(error_key, None)
                        st.success("Audio transcribed successfully! You can review or edit the text below.")
                        st.rerun()
                except ValueError as exc:
                    st.session_state[error_key] = str(exc)
                    st.error(str(exc))
                    if st.button("Retry transcription", key="retry-staff-transcription"):
                        st.session_state.pop(digest_key, None)
                        st.rerun()
                except Exception:
                    st.session_state[error_key] = "Transcription failed unexpectedly. Please retry the recording."
                    logging.getLogger(__name__).exception("Staff voice transcription failed")
                    st.error(st.session_state[error_key])
                    if st.button("Retry transcription", key="retry-staff-transcription"):
                        st.session_state.pop(digest_key, None)
                        st.rerun()
        elif st.session_state.get(error_key):
            st.error(st.session_state[error_key])
            if st.button("Retry transcription", key="retry-staff-transcription"):
                st.session_state.pop(digest_key, None)
                st.rerun()
    else:
        st.session_state.pop("last_audio_digest", None)
        st.session_state.pop("audio_transcription_error", None)
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
        st.session_state["safety"] = assess_safety(text)
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
        st.error(f"{safety['severity'].replace('_', ' ').title()} · score {safety['risk_score']}/100 · "
                 f"human review required · rules {', '.join(safety['rule_ids'])}")
        st.write({"Hazard categories": safety["hazard_categories"], "Evidence": safety["evidence"],
                  "Reasons": safety["reasons"], "Context": safety["context_status"]})
        st.caption(safety["message"] + " " + str(safety.get("threshold_status", "")))

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
            with st.container(border=True):
                st.markdown(f"**{part['issue']}** · {part['text']}")
                part_route = part["suggestion"]
                dept = selected_department(part_route)
                if dept:
                    evidence = part_route.get("evidence", {}).get(dept, [])
                    st.write(f"Department suggestion: {dept} · supporting words: {evidence}")
                    try:
                        explanation = generate_grounded_explanation(part["text"], dept)
                        st.caption(f"Responsibility source: {explanation['rule_source']} · "
                                   f"{explanation['verification']}")
                    except ValueError as exc:
                        st.caption(f"No responsibility-table explanation matched: {exc}")
                else:
                    st.warning("No supported department suggestion for this issue; human review is needed.")
                part_safety = assess_safety(part["text"])
                if part_safety["safety_flag"]:
                    st.warning(f"Issue safety suggestion: {part_safety['severity'].replace('_', ' ').title()} · "
                               f"evidence: {part_safety['evidence']}")
                for conflict in part.get("ownership_conflicts", []):
                    st.info(f"Possible ownership overlap: {conflict['owner']} / {conflict['overlap_owner']} · "
                            f"{conflict['verification']} · {conflict['source']}")
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
            cid = insert_public_submission(rec, rt, submitted_by=user["id"],
                                           split_proposal=sp if sp["is_compound"] else None)
            if photo is not None:
                save_attachment(cid, photo, user["role"], actor_user=user)
            if confirm_split:
                child_ids = splitter.confirm_split(cid, rec["case_id"], sp["parts"], user["role"],
                                                   actor_email=user.get("email"), actor_user=user)
                st.info(f"Human-confirmed split saved as {len(child_ids)} linked sub-issues.")
            st.success(f"Saved as {rec['case_id']}")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
