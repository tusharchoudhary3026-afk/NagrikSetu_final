from datetime import datetime, timedelta, timezone

import pytest

from core.db import execute, fetch_all, insert_complaint


def _geo_case(case_id, *, location, issue="Electricity", area_name="Gole Ka Mandir",
              area_code="mp-gwalior-gole-ka-mandir", created_at):
    cid = insert_complaint({
        "case_id": case_id, "original_text": "Streetlight is broken", "language": "english",
        "normalized_text": "streetlight is broken", "channel": "portal", "location": location,
        "issue": issue, "missing_fields": "", "is_synthetic": 1,
        "state_code": "mp" if area_code else None, "state_name": "Madhya Pradesh" if area_code else None,
        "city_code": "mp-gwalior" if area_code else None, "city_name": "Gwalior" if area_code else None,
        "area_code": area_code, "area_name": area_name if area_code else None,
    })
    execute("UPDATE complaints SET created_at=? WHERE id=?", (created_at.isoformat(), cid))
    return cid


def _approved_gwalior_reviewer(staff_user):
    from core.geo import configure_staff_location

    reviewer = staff_user("reviewer", "Grievance Cell")
    configure_staff_location(reviewer, "mp", "mp-gwalior", "mp-gwalior-gole-ka-mandir")
    return reviewer


def test_feature_flags_are_explicit_and_rbac_cannot_be_disabled(monkeypatch):
    from core.config import feature_enabled

    monkeypatch.delenv("NYAYAFLOW_FEATURE_VOICE", raising=False)
    assert feature_enabled("voice") is True
    monkeypatch.setenv("NYAYAFLOW_FEATURE_VOICE", "false")
    assert feature_enabled("voice") is False
    monkeypatch.setenv("NYAYAFLOW_FEATURE_RBAC", "0")
    assert feature_enabled("rbac") is True


def test_hotspot_signals_use_canonical_area_keys_and_documented_rule(db, staff_user):
    from core.hotspots import detect_hotspot_signals

    reviewer = _approved_gwalior_reviewer(staff_user)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old = now - timedelta(days=9)
    current = now - timedelta(days=1)
    for i in range(2):
        _geo_case(f"HOT-BASE-{i}", location=" Gole   Ka Mandir ", created_at=old)
    current_ids = [_geo_case(f"HOT-CURRENT-{i}", location="gole ka mandir", created_at=current)
                   for i in range(5)]

    result = detect_hotspot_signals(reviewer, window_days=7, as_of=now)

    assert result["status"] == "ready"
    assert len(result["hotspots"]) == 1
    signal = result["hotspots"][0]
    assert signal["current_count"] == 5
    assert signal["baseline_count"] == 2
    assert signal["complaint_ids"] == current_ids
    assert "at least 2x baseline" in signal["rule"]
    assert signal["comparison_start"] and signal["comparison_end"]
    assert signal["data_label"].startswith("SIMULATED / SYNTHETIC")


def test_hotspot_signals_report_insufficient_data_and_keep_unknown_bucket(db, staff_user):
    from core.hotspots import detect_hotspot_signals

    reviewer = _approved_gwalior_reviewer(staff_user)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    result = detect_hotspot_signals(reviewer, window_days=7, as_of=now)
    assert result["status"] == "insufficient_data"
    for i in range(5):
        _geo_case(f"HOT-UNKNOWN-{i}", location="unknown", area_name=None,
                  area_code=None, created_at=now - timedelta(days=1))
    result = detect_hotspot_signals(reviewer, window_days=7, as_of=now)
    assert any(item["area"] == "Unknown area" for item in result["hotspots"])


def test_citizen_review_request_records_category_and_outcome_for_owner(db, staff_user):
    from core.auth import register_user
    from core.challenges import create_challenge, resolve_challenge
    from core.portal import list_own_complaints

    resident = register_user("review-status@example.test", "Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    complaint_id = insert_complaint({
        "case_id": "CASE-REVIEW-STATUS", "original_text": "The streetlight is broken",
        "language": "english", "normalized_text": "streetlight broken", "channel": "portal",
        "location": "Gole Ka Mandir", "issue": "Electricity", "missing_fields": "",
        "is_synthetic": 1, "submitted_by": resident["id"],
    })
    request_id = create_challenge(complaint_id, "citizen", "Wrong department", citizen_user_id=resident["id"],
                                  reason_category="wrong_department")
    before = fetch_all("SELECT event_type,final_dept FROM ledger WHERE complaint_id=? ORDER BY id", (complaint_id,))
    assert fetch_all("SELECT reason_category FROM challenges WHERE id=?", (request_id,))[0]["reason_category"] == "wrong_department"
    assert list_own_complaints(resident["id"])[0]["latest_review_status"] == "open"

    reviewer = staff_user("reviewer", "Grievance Cell")
    with pytest.raises(ValueError, match="reason"):
        resolve_challenge(request_id, "reviewer", "", "accept", actor_user=reviewer)
    resolve_challenge(request_id, "reviewer", "Reviewed the route", "accept", actor_user=reviewer)

    case = list_own_complaints(resident["id"])[0]
    assert case["latest_review_status"] == "resolved"
    assert case["latest_review_outcome"] == "accept"
    assert case["latest_review_resolution"] == "Reviewed the route"
    after = fetch_all("SELECT event_type,final_dept FROM ledger WHERE complaint_id=? ORDER BY id", (complaint_id,))
    assert after[:len(before)] == before
    assert [row["event_type"] for row in after[-2:]] == ["challenge_resolved", "accept"]


def test_review_split_action_creates_children_atomically_and_preserves_parent(db, staff_user):
    from core.challenges import create_challenge, resolve_challenge
    from core.db import insert_suggestion

    reviewer = staff_user("reviewer", "Grievance Cell")
    complaint_id = insert_complaint({
        "case_id": "CASE-REVIEW-SPLIT", "original_text": "Streetlight is off and garbage is piled up",
        "language": "english", "normalized_text": "streetlight off garbage piled up", "channel": "portal",
        "location": "Ward 4", "issue": "unknown", "missing_fields": "", "is_synthetic": 1,
    })
    insert_suggestion(complaint_id, "baseline", [], {}, 0, True)
    # Use a synthetic resident so the request is authenticated and owner-scoped.
    from core.auth import register_user
    resident = register_user("split-request@example.test", "Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    execute("UPDATE complaints SET submitted_by=? WHERE id=?", (resident["id"], complaint_id))
    request_id = create_challenge(complaint_id, "citizen", "There are two issues",
                                  citizen_user_id=resident["id"], reason_category="multiple_issues")
    parts = [
        {"text": "Streetlight is off", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
        {"text": "Garbage is piled up", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
    ]

    resolve_challenge(request_id, "reviewer", "Split after review", "split", actor_user=reviewer,
                      split_parts=parts)

    parent = fetch_all("SELECT original_text,is_active FROM complaints WHERE id=?", (complaint_id,))[0]
    children = fetch_all("SELECT parent_id,original_text FROM complaints WHERE parent_id=? AND is_active=1 ORDER BY id",
                         (complaint_id,))
    events = fetch_all("SELECT event_type FROM ledger WHERE complaint_id=? ORDER BY id", (complaint_id,))
    assert parent == {"original_text": "Streetlight is off and garbage is piled up", "is_active": 1}
    assert len(children) == 2 and all(row["parent_id"] == complaint_id for row in children)
    assert events[-2:][0]["event_type"] == "challenge_resolved"
    assert events[-1]["event_type"] == "split_confirmed"


def test_correction_list_is_scoped_and_training_version_is_reported(db, staff_user):
    from core.corrections import list_corrections, review_correction
    from core.auth import register_user
    from core.reviews import submit_review
    from core.retraining import run_offline_retraining_experiment

    reviewer = staff_user("reviewer", "Grievance Cell")
    resident = register_user("correction-owner@example.test", "Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    complaint_id = insert_complaint({
        "case_id": "CASE-CORRECTION-LIST", "original_text": "Streetlight is broken",
        "language": "english", "normalized_text": "streetlight broken", "channel": "portal",
        "location": "unknown", "issue": "Electricity", "missing_fields": "", "is_synthetic": 1,
        "submitted_by": resident["id"],
    })
    from core.db import insert_suggestion
    insert_suggestion(complaint_id, "baseline", [{"dept": "Roads Dept", "score": .8}], {}, .8, False)
    submit_review(complaint_id, "reviewer", "edit", final_dept="Electricity Dept",
                  reason="Matched streetlight issue", reason_code="wrong_category",
                  actor_email=reviewer["email"], actor_user=reviewer)
    correction = list_corrections(reviewer)[0]
    assert correction["label_source"] == "reviewer_override"
    assert correction["review_status"] is None
    review_correction(correction["id"], reviewer["name"], "reviewer", "rejected",
                      "Label needs more evidence", actor_email=reviewer["email"], actor_user=reviewer)
    assert list_corrections(reviewer)[0]["review_status"] == "rejected"
    experiment = run_offline_retraining_experiment()
    assert experiment["status"] == "insufficient_verified_examples"
    assert experiment["dataset_version"].startswith("verified-corrections-")
    assert experiment["model_saved"] is False and experiment["deployed"] is False


def test_photo_failure_can_be_retried_without_losing_saved_complaint(db, monkeypatch, tmp_path):
    import io
    import secrets
    from PIL import Image
    from core.attachment import save_attachments
    from core.auth import register_user
    from core.db import insert_public_submission
    from core import intake, router_baseline

    monkeypatch.setenv("NYAYAFLOW_ATTACHMENT_KEY", secrets.token_hex(32))
    monkeypatch.setattr("core.attachment.ATTACHMENT_DIR", tmp_path)
    resident = register_user("photo-retry@example.test", "Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    rec = intake.process("Streetlight is broken", "portal", "Gole Ka Mandir")
    rec["case_id"] = "CASE-PHOTO-RETRY"
    complaint_id = insert_public_submission(rec, router_baseline.route(rec["original_text"]),
                                            submitted_by=resident["id"])
    photo = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(photo, format="PNG")
    photo.name = "evidence.png"
    db.execute("CREATE TRIGGER reject_attachment BEFORE INSERT ON attachments "
               "BEGIN SELECT RAISE(ABORT, 'temporary upload failure'); END")
    with pytest.raises(Exception, match="temporary upload failure"):
        save_attachments(complaint_id, [photo], "public", actor_user=resident)
    assert fetch_all("SELECT original_text FROM complaints WHERE id=?", (complaint_id,))[0]["original_text"] == "Streetlight is broken"
    assert fetch_all("SELECT id FROM attachments WHERE complaint_id=?", (complaint_id,)) == []
    db.execute("DROP TRIGGER reject_attachment")

    saved = save_attachments(complaint_id, [photo], "public", actor_user=resident)
    assert len(saved) == 1
    assert fetch_all("SELECT COUNT(*) AS n FROM attachments WHERE complaint_id=?", (complaint_id,))[0]["n"] == 1


def test_new_pages_are_role_gated_and_feature_flags_can_hide_them(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import begin_staff_registration, verify_staff_registration
    from core.db import execute

    email = "flags-reviewer@staff.nagriksetu-demo.example"
    execute("INSERT INTO staff_directory(staff_email,full_name,role,department,status,registered,tag) "
            "VALUES(?,?,?,'Grievance Cell','active',0,'SYNTHETIC')", (email, "Flag Reviewer", "reviewer"))
    flow = begin_staff_registration(email, "Flag Reviewer", "Verify!Example42", "Verify!Example42", code="123456")
    reviewer = verify_staff_registration(email, flow["simulated_code"])
    root = Path(__file__).resolve().parent.parent
    for page in ("pages/13_Hotspots.py", "pages/14_Correction_Dataset.py", "pages/15_Model_Evaluation.py"):
        app = AppTest.from_file(root / "app.py", default_timeout=30)
        app.session_state["authenticated_user"] = reviewer
        app.run()
        app.switch_page(page).run()
        assert not app.exception, f"{page}: {[error.message for error in app.exception]}"


def test_routing_review_queue_page_has_split_action_and_staff_assignment_ui(db, staff_user):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import register_user
    from core.challenges import create_challenge
    from core.db import insert_suggestion

    reviewer = staff_user("reviewer", "Grievance Cell")
    from core.geo import configure_staff_location
    configure_staff_location(reviewer, "mp", "mp-gwalior", "mp-gwalior-gole-ka-mandir")
    resident = register_user("queue-citizen@example.test", "Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    complaint_id = _geo_case("CASE-QUEUE-SPLIT", location="Gole Ka Mandir",
                             created_at=datetime.now(timezone.utc))
    execute("UPDATE complaints SET original_text=?,submitted_by=? WHERE id=?",
            ("Streetlight is off and garbage is piled up", resident["id"], complaint_id))
    insert_suggestion(complaint_id, "baseline", [], {}, 0, True)
    create_challenge(complaint_id, "citizen", "Two issues need different departments",
                     citizen_user_id=resident["id"], reason_category="multiple_issues")
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = reviewer
    app.run()
    app.switch_page("pages/11_Request_Review_Queue.py").run()
    assert not app.exception
    assert any("Claim request" in button.label for button in app.button)
    app.button(key="claim-1").click().run()
    assert not app.exception
    assert "Split and reroute issues" in app.selectbox(key="outcome-1").options

