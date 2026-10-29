import json
import logging
import uuid

import streamlit as st

from core.attachment import save_attachments, validate_attachments
from core.auth import require_role
from core import intake, router_baseline, splitter
from core.config import SIM_LABEL, feature_enabled
from core.db import init_db, insert_public_submission
from core.intake import normalize_location
from core.portal import list_own_complaints, list_own_split_children
from core.privacy import mask_text
from core.safety import flag_safety
from core.security import attachment_encryption_status
from core.ui import apply_demo_style, render_hero
from core.geo import known_area_for_location, partial_geography_names
from core.geo_ui import render_geography_picker
from core.speech import validate_transcript_submission
from core.speech_component import speech_input
from core.suggestions import selected_department


class _StoredUpload:
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data

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
    speech_used = False
    transcript_confirmed = False
    if voice_enabled:
        st.caption("Type your complaint or describe it by voice. Please check names, locations and numbers.")
        consent = st.checkbox("I understand browser speech recognition may send audio to its provider; NagrikSetu does not store audio.",
                              key="public-speech-consent")
        with st.expander("Voice input (TRY) · optional", expanded=False):
            st.caption("If voice input is unavailable, type your complaint in the box below. Speech is never submitted automatically.")
            speech = speech_input(enabled=consent, key="public-complaint-speech", default=None)
        if speech and speech.get("id") != st.session_state.get("public_last_speech_id"):
            transcript = speech.get("transcript", "")
            if transcript:
                st.session_state["public_complaint_text"] = transcript
                st.session_state["public_speech_transcript_pending"] = True
                st.session_state["public_last_speech_id"] = speech.get("id")
        speech_used = bool(st.session_state.get("public_speech_transcript_pending"))
    with st.form("public_complaint"):
        text = st.text_area("Review transcript" if speech_used else "What is the problem?", height=140,
                            max_chars=intake.MAX_COMPLAINT_CHARS,
                            placeholder="For example: A streetlight is not working near the community center.",
                            key="public_complaint_text")
        if speech_used:
            transcript_confirmed = st.checkbox("I reviewed and confirm this transcript", key="public-speech-confirmed")
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
        submitted = st.form_submit_button("Review extracted details", type="primary", width="stretch")
    if submitted:
        # A new intake must start with its own extracted values. Streamlit keeps
        # keyed widgets across reruns, which otherwise carries an old category
        # or location into the next complaint preview.
        st.session_state.pop("public_intake_preview", None)
        for key in ("public-preview-issue", "public-preview-location", "public-preview-action",
                    "public-preview-confirmed"):
            st.session_state.pop(key, None)
        for key in [key for key in st.session_state if key.startswith("public-preview-split-issue-")]:
            st.session_state.pop(key, None)
        try:
            text = validate_transcript_submission(text, speech_used, transcript_confirmed)
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
            st.session_state.pop("last_public_submission", None)
            st.session_state["public_intake_preview"] = {
                "rec": rec,
                "route": route,
                "split": splitter.propose_split(text),
                "safety": flag_safety(text),
                "uploads": [{"name": str(getattr(item, "name", "photo")), "data": item.getvalue()}
                            for item in (attachments or []) if item is not None],
            }
            st.session_state["public_speech_transcript_pending"] = False
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Public complaint submission failed")
            st.error("The complaint details could not be prepared. Please check them and try again.")

    preview = st.session_state.get("public_intake_preview")
    if preview:
        rec = preview["rec"]
        st.subheader("Review extracted details before submission")
        st.caption("Your original complaint remains unchanged. You can correct these extracted fields; a correction does not change the routing suggestion.")
        st.markdown("**Original complaint text**")
        st.write(mask_text(rec["original_text"]))
        issue_options = ["unknown", *(category for category, _ in intake.ISSUE_CATEGORIES)]
        if "public-preview-issue" not in st.session_state:
            st.session_state["public-preview-issue"] = rec["issue"] if rec["issue"] in issue_options else "unknown"
        if "public-preview-location" not in st.session_state:
            st.session_state["public-preview-location"] = rec["location"] if rec["location"] != "unknown" else ""
        action_options = ["unspecified", "repair", "clean", "clear", "inspect"]
        if "public-preview-action" not in st.session_state:
            st.session_state["public-preview-action"] = rec["requested_action"] or "unspecified"
        if preview["split"]["is_compound"]:
            issue = "multiple issues"
            st.subheader("Possible separate issues · proposal only")
            st.caption("These are grounded in your text. Staff must confirm any split; nothing is split automatically.")
            for index, part in enumerate(preview["split"]["parts"]):
                with st.container(border=True):
                    st.markdown(f"**Possible issue {index + 1}**")
                    st.write(part["text"])
                    st.caption(f"Original text span: characters {part['start']}–{part['end']}")
                    key = f"public-preview-split-issue-{index}"
                    if key not in st.session_state:
                        st.session_state[key] = part["issue"] if part["issue"] in issue_options else "unknown"
                    st.selectbox("Issue category", issue_options, key=key)
                    route = part["suggestion"]
                    if route["abstained"]:
                        if part.get("ownership_conflicts"):
                            st.warning("Possible ownership conflict · manual staff review required; no department is selected automatically.")
                        else:
                            st.warning("No supported department suggestion for this issue; staff review is needed.")
                    else:
                        dept = selected_department(route)
                        evidence = route["evidence"].get(dept, [])
                        st.write(f"Department suggestion: **{dept}** · evidence in this text: {evidence}")
                    for overlap in part.get("ownership_conflicts", []):
                        st.warning(f"Possible ownership overlap: {overlap['owner']} / {overlap['overlap_owner']} · "
                                   f"{overlap['verification']} · {overlap['source']}")
        else:
            issue = st.selectbox("Issue category", issue_options, key="public-preview-issue")
        corrected_location = st.text_input("Street or nearby landmark", key="public-preview-location",
                                           help="Leave blank if you do not know the location.")
        action = st.selectbox("Requested action", action_options, key="public-preview-action")
        st.caption(f"Detected language: {rec['language']} · channel: {rec['channel']}")
        st.caption(f"Missing information: {rec.get('missing_fields') or 'None recorded'}")
        question = intake.clarification_question(rec)
        if question:
            st.info(f"One helpful detail for staff: {question}")
        if not preview["split"]["is_compound"] and preview["route"]["abstained"]:
            st.warning("No supported route was found. This case will wait in the human review queue.")
        elif not preview["split"]["is_compound"]:
            st.markdown("**Routing suggestions · recommendation only**")
            for suggestion in preview["route"]["top3"]:
                evidence = preview["route"]["evidence"].get(suggestion["dept"], [])
                st.write(f"{suggestion['dept']} · evidence: {evidence}")
        elif preview["split"]["parts"]:
            st.info("The complete complaint stays unassigned until staff confirms or edits the proposed split. Per-issue routes are recommendations only.")
        elif not preview["split"]["parts"]:
            st.info(preview["split"]["note"] + ". The original complaint will remain intact for staff review.")
            for overlap in preview["split"].get("ownership_conflicts", []):
                st.warning(f"Possible ownership overlap: {overlap['owner']} / {overlap['overlap_owner']} · "
                           f"{overlap['verification']} · {overlap['source']}")
        if preview["safety"]["flagged"]:
            st.warning("This report matches a safety-review rule and will be shown to staff for human attention.")
        confirmed = st.checkbox("I reviewed the extracted details and confirm they are ready to submit.",
                                key="public-preview-confirmed")
        confirm_col, edit_col = st.columns(2)
        if confirm_col.button("Confirm and submit complaint", type="primary", key="public-preview-submit"):
            if not confirmed:
                st.error("Confirm the extracted details before submitting.")
            else:
                try:
                    final_rec = dict(rec)
                    final_rec["issue"] = ("multiple issues" if preview["split"]["is_compound"]
                                           else None if issue == "unknown" else issue)
                    final_rec["location"] = (corrected_location if corrected_location.strip() else
                                              (rec.get("area_name") if rec.get("area_code") else "unknown"))
                    final_rec["location_normalized"] = normalize_location(final_rec["location"])
                    final_rec["requested_action"] = None if action == "unspecified" else action
                    missing = []
                    if final_rec["location"] == "unknown":
                        missing.append("location")
                    if not final_rec["issue"]:
                        missing.append("issue")
                    if not final_rec.get("area_code"):
                        missing.append("service_area")
                    final_rec["missing_fields"] = ",".join(missing)
                    edits = {}
                    for field in ("issue", "location", "requested_action"):
                        before, after = rec.get(field), final_rec.get(field)
                        if before != after:
                            edits[field] = {"extracted": before, "confirmed": after}
                    final_rec["extraction_corrections"] = edits
                    final_rec["case_id"] = f"CASE-{uuid.uuid4().hex[:12].upper()}"
                    split_proposal = None
                    if preview["split"]["is_compound"]:
                        split_proposal = dict(preview["split"])
                        split_proposal["parts"] = [dict(part, issue=st.session_state.get(
                            f"public-preview-split-issue-{index}", part["issue"]))
                            for index, part in enumerate(preview["split"]["parts"])]
                    submission_route = splitter.parent_route_for_submission(preview["route"], split_proposal)
                    complaint_id = insert_public_submission(final_rec, submission_route,
                                                            submitted_by=user["id"],
                                                            split_proposal=split_proposal)
                    attachment_warning = None
                    if preview["uploads"]:
                        try:
                            copied_uploads = [_StoredUpload(item["name"], item["data"])
                                              for item in preview["uploads"]]
                            save_attachments(complaint_id, copied_uploads, user["role"], actor_user=user)
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "Photo storage failed after complaint %s was committed", final_rec["case_id"])
                            attachment_warning = "The complaint was saved, but one or more photos could not be attached."
                            st.session_state["public_pending_photo_upload"] = preview["uploads"]
                    st.session_state["last_public_submission"] = {
                        "case_id": final_rec["case_id"],
                        "question": intake.clarification_question(final_rec),
                        "area": final_rec.get("area_name"),
                        "attachment_warning": attachment_warning,
                        "complaint_id": complaint_id,
                    }
                    st.session_state.pop("public_intake_preview", None)
                    st.session_state.pop("public-preview-confirmed", None)
                    st.session_state.pop("public-preview-issue", None)
                    st.session_state.pop("public-preview-location", None)
                    st.session_state.pop("public-preview-action", None)
                    for key in [key for key in st.session_state if key.startswith("public-preview-split-issue-")]:
                        st.session_state.pop(key, None)
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                except Exception:
                    logging.getLogger(__name__).exception("Public complaint confirmation failed")
                    st.error("The complaint could not be saved. Your text is still here; please retry.")
        if edit_col.button("Edit complaint details", key="public-preview-edit"):
            st.session_state.pop("public_intake_preview", None)
            st.session_state.pop("public-preview-confirmed", None)
            st.session_state.pop("public-preview-issue", None)
            st.session_state.pop("public-preview-location", None)
            st.session_state.pop("public-preview-action", None)
            for key in [key for key in st.session_state if key.startswith("public-preview-split-issue-")]:
                st.session_state.pop(key, None)
            st.rerun()

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
            if row.get("split_status"):
                st.caption(f"Split review: {row['split_status'].replace('_', ' ')} · staff confirmation required")
                if row.get("split_status") == "proposed" and row.get("split_proposal_json"):
                    try:
                        proposal_parts = json.loads(row["split_proposal_json"]).get("parts", [])
                    except (TypeError, json.JSONDecodeError):
                        proposal_parts = []
                    for part in proposal_parts:
                        dept = selected_department(part.get("suggestion"))
                        st.write(f"Possible issue: {part.get('text', '')} · {part.get('issue', 'unknown')} · "
                                 f"{dept or 'Human review needed'}")
                for child in list_own_split_children(user["id"], row["id"]):
                    state = "active" if child["is_active"] else "merged back"
                    st.write(f"Sub-issue ({state}): {child['original_text']} · {child.get('issue') or 'unknown'}")
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
