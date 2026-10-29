import io
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from PIL import Image

from core import auth, duplicates
from core.analytics import get_case_reassignment_timeline, get_reassignment_breakdown
from core.attachment import get_attachments, save_attachment, read_attachment, migrate_legacy_attachments
from core.speech import validate_transcript_submission
from core.security import attachment_encryption_status
from core.challenges import create_challenge, resolve_challenge
from core.db import execute, fetch_all, insert_complaint, insert_suggestion
from core import db as dbm
from core.analytics import detect_emerging_hotspots
from core.ledger import history, log_event
from core.privacy import mask_text
from core.reviews import submit_review
from core.safety import flag_safety, evaluate_safety_rubric
from core.explanation import generate_grounded_explanation
from core.corrections import load_verified_corrections, review_correction
from core.verify_split import verify_frozen_test_split
from core.splitter import confirm_split, merge_back
from core.audit import generate_audit_export
from core.intake import process
from core.retraining import run_offline_retraining_experiment


def complaint(text, case_id, location="unknown", issue="road", submitted_by=None):
    return insert_complaint({
        "case_id": case_id, "parent_id": None, "original_text": text,
        "language": "english", "normalized_text": text.lower(), "channel": "portal",
        "location": location, "issue": issue, "requested_action": None,
        "missing_fields": "", "is_synthetic": 1,
    }, submitted_by=submitted_by)


def test_duplicate_candidate_evidence_and_no_automatic_link(db):
    first = complaint("Large pothole on Station Road", "CASE-1", "Station Road")
    same_street_different_day = complaint("Large pothole on Station Road", "CASE-2", "Station Road")
    candidates = duplicates.find_candidates(first, limit=10)
    row = next(c for c in candidates if c["complaint_id"] == same_street_different_day)
    assert row["decision"] == "needs_review"
    assert row["text_score"] > 0
    assert row["location_match"] is True
    assert fetch_all("SELECT * FROM incident_links") == []


def test_duplicate_decision_requires_reason_and_preserves_reversible_history(db, staff_user):
    reviewer = staff_user("reviewer")
    first = complaint("Pothole at Station Road", "CASE-1", "Station Road")
    second = complaint("Pothole at Station Road", "CASE-2", "Station Road")
    with pytest.raises(ValueError):
        duplicates.record_decision(first, second, "same_incident", "reviewer", "", actor_user=reviewer)
    duplicates.record_decision(first, second, "same_incident", "reviewer", "Same defect reported",
                               reason_code="same_incident", actor_user=reviewer)
    before = fetch_all("SELECT * FROM incident_links")
    duplicates.record_decision(first, second, "unlink", "reviewer", "Reports are from different days",
                               reason_code="reopen_after_review", actor_user=reviewer)
    with pytest.raises(ValueError, match="do not have an active incident link"):
        duplicates.record_decision(first, second, "unlink", "reviewer", "Already unlinked",
                                   reason_code="reopen_after_review", actor_user=reviewer)
    links = fetch_all("SELECT * FROM incident_links")
    assert links[0] == before[0] and len(links) == 2 and links[1]["active"] == 0
    events = fetch_all("SELECT event_type FROM ledger WHERE complaint_id=?", (first,))
    assert [e["event_type"] for e in events] == [
        "duplicate_confirmed", "duplicate_linked", "duplicate_unlinked"
    ]


def test_duplicate_candidate_different_date_is_suppressed_and_scores_are_not_probabilities(db):
    now = datetime.now()
    first = complaint("Overflowing drain at Ward 4", "CASE-1", "Ward 4")
    second = complaint("Overflowing drain at Ward 4", "CASE-2", "Ward 4")
    execute("UPDATE complaints SET created_at=? WHERE id=?", ((now - timedelta(days=40)).isoformat(), first))
    execute("UPDATE complaints SET created_at=? WHERE id=?", (now.isoformat(), second))
    row = next(c for c in duplicates.find_candidates(first, time_window_days=7) if c["complaint_id"] == second)
    assert row["time_difference_days"] > 7
    assert row["score"] < row["text_score"]
    assert row["score_kind"] == "candidate_score_not_probability"


def test_canonical_incident_group_is_shared_across_confirmed_links(db, staff_user):
    reviewer = staff_user("reviewer")
    first = complaint("Pothole at Station Road", "CASE-1", "Station Road")
    second = complaint("Pothole beside Station Road", "CASE-2", "Station Road")
    third = complaint("Pothole across Station Road", "CASE-3", "Station Road")
    duplicates.record_decision(first, second, "same_incident", "reviewer", "Same damage site",
                               reason_code="same_incident", actor_user=reviewer)
    duplicates.record_decision(first, third, "same_incident", "reviewer", "Same damage site",
                               reason_code="same_incident", actor_user=reviewer)
    links = fetch_all("SELECT incident_id FROM incident_links WHERE active=1 ORDER BY id")
    assert len(links) == 2 and links[0]["incident_id"] == links[1]["incident_id"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        execute("DELETE FROM incident_links WHERE id=?", (1,))


def test_duplicate_candidates_ignore_inactive_split_children(db, staff_user):
    reviewer = staff_user("reviewer")
    parent_id = complaint("Streetlight is broken and garbage is piled near park", "CASE-INACTIVE-CANDIDATE")
    children = confirm_split(parent_id, "CASE-INACTIVE-CANDIDATE", [
        {"text": "Streetlight is broken", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
        {"text": "Garbage is piled near park", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
    ], "reviewer", actor_user=reviewer)
    merge_back(parent_id, "reviewer", "Reviewed and merged the split back", actor_user=reviewer)
    assert all(row["complaint_id"] not in children for row in duplicates.find_candidates(parent_id))
    with pytest.raises(ValueError, match="Complaint not found"):
        duplicates.find_candidates(children[0])


def test_rbac_denies_unauthorized_and_unknown_roles():
    assert auth.verify_permission("reviewer", "can_review")
    assert not auth.verify_permission("auditor", "can_review")
    with pytest.raises(PermissionError):
        auth.enforce_rbac("operator", "can_review")
    with pytest.raises(PermissionError):
        auth.enforce_rbac("admin", "can_review")


def test_first_account_setup_and_authentication(db):
    with pytest.raises(PermissionError):
        auth.bootstrap_user("reviewer", "long-password-example", "reviewer", "bad", "setup-token")
    auth.bootstrap_user("reviewer", "long-password-example", "reviewer", "setup-token", "setup-token")
    assert auth.authenticate("reviewer", "long-password-example")["role"] == "reviewer"
    assert auth.authenticate("reviewer", "incorrect-password") is None
    with pytest.raises(ValueError, match="already complete"):
        auth.bootstrap_user("second", "another-long-password", "reviewer", "setup-token", "setup-token")


def test_active_session_reloads_role_and_invalidates_disabled_accounts(db):
    auth.bootstrap_user("reviewer", "long-password-example", "reviewer", "setup-token", "setup-token")
    account = auth.authenticate("reviewer", "long-password-example")
    assert auth.get_active_user(account["id"])["role"] == "reviewer"
    execute("UPDATE users SET role='auditor' WHERE id=?", (account["id"],))
    assert auth.get_active_user(account["id"])["role"] == "auditor"
    execute("UPDATE users SET active=0 WHERE id=?", (account["id"],))
    assert auth.get_active_user(account["id"]) is None


def test_db_initialization_migrates_preexisting_prototype_tables(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE challenges (id INTEGER PRIMARY KEY, complaint_id INTEGER, raised_by TEXT, "
                 "reason TEXT, status TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE corrections (id INTEGER PRIMARY KEY, complaint_id INTEGER, text TEXT, "
                 "wrong_dept TEXT, correct_dept TEXT, created_at TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    assert {"resolved_by", "resolution", "outcome", "resolved_at"}.issubset(
        {row["name"] for row in dbm.fetch_all("PRAGMA table_info(challenges)")})
    assert {"reason_code", "label_source"}.issubset(
        {row["name"] for row in dbm.fetch_all("PRAGMA table_info(corrections)")})


def test_db_initialization_adds_duplicate_reason_code_idempotently(tmp_path, monkeypatch):
    path = tmp_path / "legacy-duplicates.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE complaints (id INTEGER PRIMARY KEY, case_id TEXT, parent_id INTEGER,
            original_text TEXT, language TEXT, normalized_text TEXT, channel TEXT, location TEXT,
            issue TEXT, requested_action TEXT, missing_fields TEXT, is_synthetic INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO complaints (id, case_id, original_text) VALUES (1, 'LEGACY-A', 'pothole');
        INSERT INTO complaints (id, case_id, original_text) VALUES (2, 'LEGACY-B', 'pothole');
        CREATE TABLE duplicate_reviews (id INTEGER PRIMARY KEY, complaint_id INTEGER,
            candidate_complaint_id INTEGER, decision TEXT, actor TEXT, reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO duplicate_reviews (complaint_id,candidate_complaint_id,decision,actor,reason)
            VALUES (1,2,'same_incident','reviewer','legacy decision');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbm, "DB_PATH", path)

    dbm.init_db()
    dbm.init_db()

    rows = dbm.fetch_all("SELECT reason_code FROM duplicate_reviews")
    assert rows[0]["reason_code"] == "legacy_reason"


def test_attachment_migration_marks_old_files_plaintext_and_denies_decryption(tmp_path, monkeypatch, staff_user):
    import secrets
    path = tmp_path / "legacy-attachments.db"
    legacy_file = tmp_path / "old.png"
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "green").save(image, format="PNG")
    legacy_file.write_bytes(image.getvalue())
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE attachments (id INTEGER PRIMARY KEY, complaint_id INTEGER, filename TEXT, "
                 "file_path TEXT, content_type TEXT, byte_size INTEGER, created_at TEXT)")
    conn.execute("INSERT INTO attachments VALUES (1, 1, 'old.png', ?, 'image/png', 20, 'now')",
                 (str(legacy_file),))
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    reviewer = staff_user("reviewer")
    complaint("Legacy photo record", "CASE-LEGACY-PHOTO")
    row = fetch_all("SELECT is_encrypted FROM attachments WHERE id=1")[0]
    assert row["is_encrypted"] == 0
    with pytest.raises(ValueError, match="legacy photo is not encrypted"):
        read_attachment(1, "reviewer", actor_user=reviewer)
    monkeypatch.setenv("NYAYAFLOW_ATTACHMENT_KEY", secrets.token_hex(32))
    assert migrate_legacy_attachments() == 1
    migrated_row = get_attachments(1, "reviewer", actor_user=reviewer)[0]
    assert migrated_row["is_encrypted"] == 1 and not legacy_file.exists()
    decrypted = Image.open(io.BytesIO(read_attachment(1, "reviewer", actor_user=reviewer)))
    assert decrypted.size == (2, 2)


def test_attachment_validation_and_review_access(db, tmp_path, monkeypatch, staff_user):
    import secrets
    monkeypatch.setenv("NYAYAFLOW_ATTACHMENT_KEY", secrets.token_hex(32))
    cid = complaint("Broken streetlight", "CASE-ATTACH")
    operator = staff_user("operator", "Electricity Dept")
    reviewer = staff_user("reviewer")
    insert_suggestion(cid, "baseline", [{"dept": "Electricity Dept", "score": .8}], {}, .8, False)
    monkeypatch.setattr("core.attachment.ATTACHMENT_DIR", tmp_path)
    upload = io.BytesIO(b"not really an image")
    upload.name = "evidence.jpg"
    with pytest.raises(ValueError, match="image"):
        save_attachment(cid, upload, actor_role="operator", actor_user=operator)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    image.name = "../../private.png"
    stored = save_attachment(cid, image, actor_role="operator", actor_user=operator)
    assert stored and (tmp_path / stored).is_file()
    with pytest.raises(PermissionError):
        get_attachments(cid, actor_role="auditor")
    assert len(get_attachments(cid, actor_role="reviewer", actor_user=reviewer)) == 1


def test_challenge_resolution_is_audited_and_reason_required(db, staff_user):
    resident = auth.register_user("challenge-resident@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    cid = complaint("Streetlight is broken", "CASE-CH", submitted_by=resident["id"])
    challenge_id = create_challenge(cid, "citizen", "Wrong department", citizen_user_id=resident["id"])
    reviewer = staff_user("reviewer")
    with pytest.raises(ValueError):
        resolve_challenge(challenge_id, "reviewer", "", "accept", actor_user=reviewer)
    resolve_challenge(challenge_id, "reviewer", "Electrical team confirmed", "edit",
                      final_dept="Electricity Dept", reason_code="wrong_category", actor_user=reviewer)
    row = fetch_all("SELECT * FROM challenges WHERE id=?", (challenge_id,))[0]
    assert (row["status"], row["resolved_by"], row["resolution"]) == (
        "resolved", "reviewer", "Electrical team confirmed")
    assert [e["event_type"] for e in history(cid)] == ["challenge", "challenge_resolved", "edit"]


def test_duplicate_open_challenges_are_rejected(db):
    resident = auth.register_user("open-resident@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    cid = complaint("Streetlight is broken", "CASE-CH-DUP", submitted_by=resident["id"])
    create_challenge(cid, "citizen", "Please review this routing", citizen_user_id=resident["id"])
    with pytest.raises(ValueError, match="already open"):
        create_challenge(cid, "citizen", "Duplicate request", citizen_user_id=resident["id"])


def test_reassignment_analytics_use_transfer_events_and_correct_timestamp_column(db):
    cid = complaint("Pothole", "CASE-A")
    log_event(cid, "suggested", "system", suggested_dept="Roads Dept")
    log_event(cid, "transfer", "reviewer", suggested_dept="Roads Dept", final_dept="Drainage Dept",
              reason="Overlapping responsibility", reason_code="overlapping_ownership")
    summary = get_reassignment_breakdown()
    assert summary["total"] == 1
    assert summary["by_reason"]["overlapping_ownership"]["count"] == 1
    timeline = get_case_reassignment_timeline(cid)
    assert timeline[-1]["created_at"]
    assert timeline[-1]["event_type"] == "transfer"


def test_hotspots_compare_equal_windows_and_label_synthetic_observations(db):
    now = datetime.now()
    baseline = complaint("Pothole near the station", "CASE-H0", "Ward 2", "Road damage")
    execute("UPDATE complaints SET created_at=? WHERE id=?", ((now - timedelta(days=8)).isoformat(), baseline))
    for number in range(3):
        cid = complaint("Pothole near the station", f"CASE-H{number+1}", "Ward 2", "Road damage")
        execute("UPDATE complaints SET created_at=? WHERE id=?",
                ((now - timedelta(days=1)).isoformat(), cid))
    rows = detect_emerging_hotspots(min_count=3, window_days=7, as_of=now)
    assert len(rows) == 1
    assert rows[0]["observed_count"] == 3 and rows[0]["baseline_count"] == 1
    assert rows[0]["status_label"] == "SIMULATED / SYNTHETIC DATA - not real municipal data"


def test_frozen_test_manifest_matches_the_data_and_rejects_changes():
    assert verify_frozen_test_split()["passed"]
    import pandas as pd
    from core.config import COMPLAINTS
    frame = pd.read_csv(COMPLAINTS, encoding="utf-8-sig", dtype=str).fillna("")
    index = frame.index[frame["split"] == "test"][0]
    frame.loc[index, "text"] = frame.loc[index, "text"] + " changed"
    assert not verify_frozen_test_split(frame)["passed"]


def test_retraining_stays_idle_until_verified_examples_are_available(db):
    result = run_offline_retraining_experiment()
    assert result["status"] == "insufficient_verified_examples"
    assert result["verified_examples"] == 0
    assert result["model_saved"] is False and result["deployed"] is False


def test_privacy_masks_name_phone_and_email():
    masked = mask_text("Contact Ravi Kumar at +91 9876543210 or ravi@example.org")
    assert "Ravi Kumar" not in masked
    assert "9876543210" not in masked
    assert "ravi@example.org" not in masked


def test_audit_export_masks_contacts_and_enforces_read_access(db, staff_user):
    cid = complaint("Streetlight", "CASE-AUDIT")
    log_event(cid, "challenge", "citizen", reason="Contact Ravi Kumar at +91 9876543210")
    with pytest.raises(PermissionError):
        generate_audit_export("operator")
    auditor = staff_user("auditor")
    export = generate_audit_export("auditor", actor_user=auditor)
    assert "Ravi Kumar" not in export and "9876543210" not in export


def test_review_override_is_atomic_and_preserves_previous_suggestion(db, staff_user):
    reviewer = staff_user("reviewer")
    cid = complaint("Streetlight broken near station road", "CASE-REVIEW")
    from core.db import insert_suggestion
    insert_suggestion(cid, "baseline", [{"dept": "Roads Dept", "score": 1}], {}, 1, 0)
    with pytest.raises(ValueError):
        submit_review(cid, "reviewer", "edit", "Electricity Dept", "because", None, actor_user=reviewer)
    assert fetch_all("SELECT * FROM ledger WHERE complaint_id=?", (cid,)) == []
    submit_review(cid, "reviewer", "edit", "Electricity Dept", "Streetlight wiring", "wrong_category",
                  actor_user=reviewer)
    event = history(cid, actor_user=reviewer)[0]
    assert event["suggested_dept"] == "Roads Dept" and event["final_dept"] == "Electricity Dept"
    assert fetch_all("SELECT wrong_dept,correct_dept FROM corrections WHERE complaint_id=?", (cid,)) == [
        {"wrong_dept": "Roads Dept", "correct_dept": "Electricity Dept"}]
    correction_id = fetch_all("SELECT id FROM corrections WHERE complaint_id=?", (cid,))[0]["id"]
    assert load_verified_corrections() == []
    review_correction(correction_id, "reviewer-1", "reviewer", "verified", "Checked against independent source",
                      actor_user=reviewer)
    assert [row["id"] for row in load_verified_corrections()] == [correction_id]
    review_correction(correction_id, "reviewer-2", "reviewer", "rejected", "Rule remains unconfirmed",
                      actor_user=reviewer)
    assert load_verified_corrections() == []


def test_safety_rubric_flags_calm_hazard_reports_for_people():
    result = flag_safety("Please inspect the exposed wire near the park; it is sparking.")
    assert result["flagged"]
    assert result["flags"][0]["status"] == "HUMAN_REVIEW_REQUIRED"
    assert "not a risk" in result["message"]
    metrics = evaluate_safety_rubric()
    assert metrics["total"] == 12 and metrics["true_positive"] == metrics["positive_denominator"] == 10
    assert metrics["false_positive"] == 0 and metrics["negative_denominator"] == 2
    assert metrics["status_label"] == "SYNTHETIC RUBRIC SANITY CHECK"


def test_grounded_explanation_needs_text_and_traceable_rule():
    explanation = generate_grounded_explanation(
        "Streetlight is broken on Station Road", "Electricity Dept")
    assert explanation["grounded_keywords"] == ["Streetlight"]
    assert explanation["verification"] == "ASSUMPTION"
    with pytest.raises(ValueError, match="supports"):
        generate_grounded_explanation("Please help", "Electricity Dept")


def test_split_confirmation_and_merge_back_keep_original_child_history(db, staff_user):
    reviewer = staff_user("reviewer")
    cid = complaint("Streetlight is broken and garbage is piled near park", "CASE-SPLIT")
    ids = confirm_split(cid, "CASE-SPLIT", [
        {"text": "Streetlight is broken", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
        {"text": "Garbage is piled near park", "suggestion": {"top3": [], "evidence": {}, "confidence": 0, "abstained": True}},
    ], "reviewer", actor_user=reviewer)
    assert len(ids) == 2
    assert merge_back(cid, "reviewer", "Reviewed and merged the split back", actor_user=reviewer) == 2
    assert fetch_all("SELECT is_active FROM complaints WHERE id IN (?,?) ORDER BY id", tuple(ids)) == [
        {"is_active": 0}, {"is_active": 0}]
    assert [event["event_type"] for event in history(cid)] == ["split_confirmed", "split_merged_back"]


def test_only_reviewer_can_confirm_compound_split(db):
    cid = complaint("Streetlight and garbage", "CASE-ROLE-SPLIT")
    with pytest.raises(PermissionError):
        confirm_split(cid, "CASE-ROLE-SPLIT", [{"text": "Streetlight"}, {"text": "Garbage"}], "operator")
    assert fetch_all("SELECT id FROM complaints WHERE parent_id=?", (cid,)) == []


def test_issue_extraction_uses_category_and_abstains_on_generic_text():
    assert process("Waterlogging on the road near the depot")["issue"] == "Drainage"
    assert process("Streetlight is broken near the market")["issue"] == "Electricity"
    assert process("Problem near my house, please fix")["issue"] == "unknown"


def test_speech_transcript_requires_confirmation_and_nonempty_text():
    from core.intake import MAX_COMPLAINT_CHARS, process

    with pytest.raises(ValueError, match="Review and confirm"):
        validate_transcript_submission("The streetlight is broken", speech_used=True, confirmed=False)
    assert validate_transcript_submission(
        "The streetlight is broken", speech_used=True, confirmed=True
    ) == "The streetlight is broken"
    assert validate_transcript_submission("Typed complaint", speech_used=False, confirmed=False) == "Typed complaint"
    entered = "  मेरे इलाके में स्ट्रीटलाइट बंद है 😊\r\n"
    assert validate_transcript_submission(entered, speech_used=False, confirmed=False) == entered
    assert validate_transcript_submission(entered, speech_used=True, confirmed=True) == entered
    with pytest.raises(ValueError, match="Enter a complaint"):
        validate_transcript_submission("  ", speech_used=False, confirmed=False)
    with pytest.raises(ValueError, match="10,000 characters or fewer"):
        process("x" * (MAX_COMPLAINT_CHARS + 1))


def test_photo_attachments_are_encrypted_at_rest_and_read_only_by_reviewers(db, monkeypatch, tmp_path, staff_user):
    import secrets
    monkeypatch.setenv("NYAYAFLOW_ATTACHMENT_KEY", secrets.token_hex(32))
    monkeypatch.setattr("core.attachment.ATTACHMENT_DIR", tmp_path / "attachments")
    operator = staff_user("operator", "Grievance Cell")
    reviewer = staff_user("reviewer")
    cid = complaint("Photo evidence", "CASE-SECURE-PHOTO")
    insert_suggestion(cid, "baseline", [{"dept": "Grievance Cell", "score": .8}], {}, .8, False)
    image = Image.new("RGB", (2, 2), color="red")
    source = io.BytesIO()
    image.save(source, format="PNG")
    raw_image = source.getvalue()
    filename = save_attachment(cid, type("Upload", (), {
        "name": "evidence.png", "getvalue": lambda self: raw_image,
    })(), "operator", actor_user=operator)
    row = get_attachments(cid, "reviewer", actor_user=reviewer)[0]
    stored = Path(row["file_path"]).read_bytes()
    assert row["is_encrypted"] == 1
    assert stored.startswith(b"NF01") and raw_image not in stored
    decrypted = Image.open(io.BytesIO(read_attachment(row["id"], "reviewer", actor_user=reviewer)))
    assert decrypted.format == "PNG" and decrypted.size == (2, 2)
    assert decrypted.convert("RGB").tobytes() == Image.open(io.BytesIO(raw_image)).convert("RGB").tobytes()
    with pytest.raises(PermissionError):
        read_attachment(row["id"], "auditor")
    assert attachment_encryption_status()["configured"] is True
    assert filename == row["filename"]


def test_photo_upload_fails_closed_without_encryption_key(db, monkeypatch, tmp_path):
    monkeypatch.delenv("NYAYAFLOW_ATTACHMENT_KEY", raising=False)
    monkeypatch.setattr("core.attachment.ATTACHMENT_DIR", tmp_path / "attachments")
    cid = complaint("Photo evidence", "CASE-NO-KEY")
    image = Image.new("RGB", (2, 2), color="blue")
    source = io.BytesIO()
    image.save(source, format="PNG")
    upload = type("Upload", (), {"name": "evidence.png", "getvalue": lambda self: source.getvalue()})()
    with pytest.raises(ValueError, match="NYAYAFLOW_ATTACHMENT_KEY"):
        save_attachment(cid, upload, "operator")
    assert fetch_all("SELECT id FROM attachments WHERE complaint_id=?", (cid,)) == []
