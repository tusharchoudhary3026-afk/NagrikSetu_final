import json
import logging
import uuid

import streamlit as st

from core.attachment import save_attachments, validate_attachments
from core.auth import require_role
from core import intake, router_baseline
from core.config import SIM_LABEL, feature_enabled
from core.db import init_db, insert_public_submission
from core.portal import list_own_complaints
from core.privacy import mask_text
from core.security import attachment_encryption_status
from core.ui import apply_demo_style, render_hero
from core.geo import known_area_for_location, partial_geography_names
from core.geo_ui import render_geography_picker
from core.speech import validate_transcript_submission
from core.sarvam_speech import transcribe_audio

init_db()
user = require_role("public")
apply_demo_style()
render_hero("Resident portal", "Submit a civic concern", "Describe what happened and where. A person makes every final routing decision.")
st.warning(SIM_LABEL)

tab_submit, tab_track = st.tabs(["Submit a complaint", "My complaints"])
with tab_submit:
    st.caption("Share only details needed to understand the issue. Do not include passwords, financial information, or highly sensitive personal data.")
    state_code, city_code, area_code = render_geography_picker("resident-complaint", allow_unknown=True)
    st.caption("Location options are prototype coverage and may not include every ward. Choose ‘Not sure’ if unknown.")
    
    voice_enabled = feature_enabled("voice")
    if voice_enabled:
        st.markdown("### 🎙️ Voice Intake (Powered by Sarvam AI)")
        st.caption("Record your complaint voice note in Hindi, English, or Hinglish.")
        
        audio_value = st.audio_input("Record your complaint voice note", key="public_sarvam_audio_recorder")
        
        if audio_value is not None:
            audio_bytes = audio_value.getvalue()
            if audio_bytes != st.session_state.get("public_last_audio_bytes"):
                with st.spinner("Transcribing audio via Sarvam AI..."):
                    try:
                        transcript = transcribe_audio(audio_bytes, audio_value.name)
                        if transcript:
                            st.session_state["public_complaint_text"] = transcript
                            st.session_state["public_last_audio_bytes"] = audio_bytes
                            st.success("Audio transcribed successfully! You can review or edit the text below.")
                            st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with st.form("public_complaint"):
        text = st.text_area("What is the problem?", height=140,
                            max_chars=intake.MAX_COMPLAINT_CHARS,
                            placeholder="For example: A streetlight is not working near the community center.",
                            key="public_complaint_text")
        location = st.text_input("Street or nearby landmark (optional)", placeholder="Street, building, or nearby landmark")
        photos_enabled = feature_enabled("photos")
        attachments = st.file_uploader("Add up to 3 photos (optional)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
                                       help="JPEG, PNG or WEBP · up to 5 MB each. Photos provide context only and are never analyzed.",
                                       disabled=not photos_enabled)
        encryption = attachment_encryption_status()
        if photos_enabled and not encryption["configured"]:
            st.info("Secure photo upload is currently unavailable. You can still submit the complaint without photos.")
        if not photos_enabled:
            st.info("Photo evidence is disabled by configuration.")
        submitted = st.form_submit_button("Review and submit", type="primary", width="stretch")
        
    if submitted:
        try:
            text = validate_transcript_submission(text, False, True)
            if attachments and not encryption["configured"]:
                raise ValueError("Photo upload is unavailable until secure storage is configured. Remove the photos and submit the text, or try later.")
            validate_attachments(attachments)
            rec = intake.process(text, "portal", location)
            geo = partial_geography_names(state_code, city_code, area_code)
            rec.update(geo)
            if not area_code:
                known_area = known_area_for_location(state_code, city_code, location)
                if known_area:
                    rec.update(known_area)
                    rec["state_name"] = geo["state_name"]
                    rec["city_name"] = geo["city_name"]
            if area_code:
                rec["location"] = location if location and location.strip() else geo["area_name"]
            if not rec.get("area_code"):
                rec["missing_fields"] = ",".join(filter(None, [rec.get("missing_fields", ""), "service_area"]))
            route = router_baseline.route(text)
            rec["case_id"] = f"CASE-{uuid.uuid4().hex[:12].upper()}"
            complaint_id = insert_public_submission(rec, route, submitted_by=user["id"])
            attachment_warning = None
            if attachments:
                try:
                    save_attachments(complaint_id, attachments, user["role"], actor_user=user)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Photo storage failed after complaint %s was committed", rec["case_id"])
                    attachment_warning = "The complaint was saved, but one or more photos could not be attached."
                    st.session_state["public_pending_photo_upload"] = [
                        {"name": str(getattr(item, "name", "photo")), "data": item.getvalue()}
                        for item in attachments if item is not None
                    ]
            st.session_state["last_public_submission"] = {"case_id": rec["case_id"],
                                                           "question": intake.clarification_question(rec),
                                                           "area": rec.get("area_name"),
                                                           "attachment_warning": attachment_warning,
                                                           "complaint_id": complaint_id}
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Public complaint submission failed")
            st.error("The complaint could not be saved. Please try again.")

    last = st.session_state.get("last_public_submission")
    if last:
        st.success(f"Your complaint was submitted. Case ID: {last['case_id']}")
        if last.get("attachment_warning"):
            st.warning(last["attachment_warning"])
            pending_upload = st.session_state.get("public_pending_photo_upload") or []
            if pending_upload and feature_enabled("photos") and st.button("Retry photo upload", key="retry-public-photo-upload"):
                class RetryUpload:
                    def __init__(self, name, data):
                        self.name, self._data = name, data

                    def getvalue(self):
                        return self._data

                try:
                    save_attachments(last["complaint_id"],
                                     [RetryUpload(item["name"], item["data"]) for item in pending_upload],
                                     user["role"], actor_user=user)
                    st.session_state.pop("public_pending_photo_upload", None)
                    st.session_state["last_public_submission"]["attachment_warning"] = None
                    st.success("Photo evidence was attached to the saved complaint.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(f"Photo upload still failed: {exc}. Your complaint text remains saved.")
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Photo retry failed for complaint %s", last["case_id"])
                    st.error("Photo upload still failed. Your complaint text remains saved; you can retry again.")
        if last.get("area"):
            st.caption(f"Service area: {last['area']}")
        if last["question"]:
            st.info(f"A useful detail to add for staff: {last['question']}")

with tab_track:
    st.subheader("Your submitted complaints")
    st.caption("Only complaints submitted by this account appear here.")
    rows = list_own_complaints(user["id"])
    if not rows:
        st.info("You have not submitted any complaints yet.")
    for row in rows:
        with st.container(border=True):
            st.markdown(f"### {row['case_id']}")
            st.write(mask_text(row["original_text"]))
            status = row.get("last_decision") or "Under review"
            if row.get("open_requests"):
                status = "Review requested"
            st.caption(f"Status: {status}")
            if row.get("location") and row["location"] != "unknown":
                st.caption(f"Location: {row['location']}")
            if row.get("state_name") or row.get("city_name") or row.get("area_name"):
                st.caption("Selected area: " + " · ".join(x for x in
                          (row.get("state_name"), row.get("city_name"), row.get("area_name")) if x))
            if row.get("abstained"):
                st.info("Awaiting human review; the system did not make a supported suggestion.")
            elif row.get("top3"):
                try:
                    top3 = json.loads(row["top3"])
                except (TypeError, json.JSONDecodeError):
                    top3 = []
                if top3:
                    st.caption(f"Suggestion: {top3[0].get('dept', 'Not available')} · final decisions are made by staff")
            if row.get("final_dept"):
                st.write(f"Latest staff decision: **{row['final_dept']}**")
            if row.get("open_requests"):
                st.caption("Your review request is awaiting a staff response.")
            else:
                st.caption("You can request a human review from the My complaints page.")