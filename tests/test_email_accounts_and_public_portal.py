import io
import sqlite3
import secrets

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest
from pathlib import Path

from core import auth
from core.attachment import read_attachment, save_attachment
from core.db import fetch_all, insert_complaint, insert_public_submission


def _confirm_public_preview(app):
    app.checkbox(key="public-preview-confirmed").set_value(True).run()
    app.button(key="public-preview-submit").click().run()
from core import db as dbm
from core.portal import list_own_complaints
from core.reviews import submit_review


def _complaint(case_id, submitted_by=None):
    return insert_complaint({
        "case_id": case_id, "parent_id": None, "original_text": "Streetlight is broken near Ward 4",
        "language": "english", "normalized_text": "streetlight broken ward 4", "channel": "portal",
        "location": "Ward 4", "issue": "electricity", "requested_action": "repair",
        "missing_fields": "", "is_synthetic": 1,
    }, submitted_by=submitted_by)


def _staff_account(email, name="Staff", role="reviewer"):
    from core.db import execute
    execute("INSERT OR IGNORE INTO staff_directory(staff_email,full_name,role,department,status,registered,tag) "
            "VALUES(?,?,?,'Grievance Cell','active',0,'SYNTHETIC')", (email, name, role))
    flow = auth.begin_staff_registration(email, name, "Secure!River72", "Secure!River72")
    return auth.verify_staff_registration(email, flow["simulated_code"])


def test_email_password_hash_and_verification():
    encoded = auth.hash_password("Secure!River72")
    assert encoded != "Secure!River72"
    assert auth.verify_password("Secure!River72", encoded)
    assert not auth.verify_password("wrong-password", encoded)


def test_email_login_locks_after_five_failed_attempts(db):
    auth.register_user("resident@example.test", "Resident", "Secure!River72",
                       password_confirm="Secure!River72")
    for _ in range(5):
        assert auth.authenticate_email("resident@example.test", "wrong-password") is None
    with pytest.raises(auth.LoginLockedError):
        auth.authenticate_email("resident@example.test", "Secure!River72")


def test_staff_role_cannot_be_requested_from_public_registration(db):
    with pytest.raises(ValueError, match="always citizen"):
        auth.register_user("not-listed@example.test", "Resident", "Secure!River72",
                           password_confirm="Secure!River72", requested_role="government")


def test_old_username_accounts_are_preserved_as_legacy_during_migration(tmp_path, monkeypatch):
    path = tmp_path / "old-accounts.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, "
                 "role TEXT, active INTEGER, created_at TEXT)")
    conn.execute("INSERT INTO users VALUES (1,'old-reviewer','unchanged-hash','reviewer',1,'2024-01-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    legacy = dbm.fetch_all("SELECT username,password_hash,role FROM legacy_users WHERE id=1")[0]
    assert legacy == {"username": "old-reviewer", "password_hash": "unchanged-hash", "role": "reviewer"}
    assert "email" in {row["name"] for row in dbm.fetch_all("PRAGMA table_info(users)")}
    assert "location_normalized" in {row["name"] for row in dbm.fetch_all("PRAGMA table_info(complaints)")}
    assert dbm.fetch_all("SELECT * FROM users") == []


def test_public_account_cannot_pass_government_role_check(db, monkeypatch):
    account = auth.register_user("resident@example.test", "Resident", "Secure!River72",
                                 password_confirm="Secure!River72")
    assert not auth.role_allowed(account, "government")
    assert auth.role_allowed(account, "public")


def test_public_can_open_portal_but_not_government_page(db):
    account = auth.register_user("resident@example.test", "Resident", "Secure!River72",
                                 password_confirm="Secure!River72")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=20)
    app.session_state["authenticated_user"] = account
    app.run()
    app.switch_page("pages/10_Public_Portal.py").run()
    assert not app.exception
    assert any("Submit a civic concern" in item.value for item in app.markdown)
    app.switch_page("pages/9_Security.py").run()
    assert not app.exception
    assert any("isn&#x27;t available to your account" in item.value for item in app.markdown)


def test_public_portal_returns_only_the_owner_cases(db):
    owner = auth.register_user("one@example.test", "One", "Secure!River72",
                               password_confirm="Secure!River72")
    other = auth.register_user("two@example.test", "Two", "Secure!River72",
                               password_confirm="Secure!River72")
    own_id = _complaint("CASE-OWN", owner["id"])
    other_id = _complaint("CASE-OTHER", other["id"])
    assert [row["case_id"] for row in list_own_complaints(owner["id"])] == ["CASE-OWN"]
    assert other_id != own_id


def test_public_submission_is_atomic_and_visible_after_area_approval(db, staff_user, monkeypatch):
    from core.geo import approve_area_request, configure_staff_location
    from core.staff_dashboard import list_staff_cases
    from pathlib import Path

    resident = auth.register_user("streetlight-resident@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    original = "  the streetlight is not working in my area \r\n"
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    app.session_state["authenticated_user"] = resident
    app.run()
    assert not app.exception
    app.selectbox(key="resident-complaint-state").select_index(1).run()
    app.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    app.selectbox(key="resident-complaint-area-mp-gwalior").select_index(2).run()
    app.text_area[0].set_value(original)
    app.form_submit_button[0].click().run()
    assert not app.exception
    assert "public_intake_preview" in app.session_state
    assert fetch_all("SELECT id FROM complaints WHERE submitted_by=? AND original_text=?",
                     (resident["id"], original)) == []
    _confirm_public_preview(app)
    assert not app.exception
    assert any("Your complaint was submitted" in item.value for item in app.success)
    case_id = app.session_state["last_public_submission"]["case_id"]
    complaint_id = fetch_all("SELECT id FROM complaints WHERE case_id=?", (case_id,))[0]["id"]

    abstained_text = "gibberish xyz 123"
    app.text_area[0].set_value(abstained_text)
    app.form_submit_button[0].click().run()
    _confirm_public_preview(app)
    assert not app.exception
    abstained_case_id = app.session_state["last_public_submission"]["case_id"]
    abstained_id = fetch_all("SELECT id FROM complaints WHERE case_id=?", (abstained_case_id,))[0]["id"]

    # The submission is committed with its suggestion and ledger event together.
    stored = fetch_all("SELECT * FROM complaints WHERE id=?", (complaint_id,))[0]
    suggestion = fetch_all("SELECT * FROM suggestions WHERE complaint_id=?", (complaint_id,))[0]
    ledger = fetch_all("SELECT * FROM ledger WHERE complaint_id=? ORDER BY id", (complaint_id,))
    assert stored["original_text"] == original
    assert stored["case_id"] == case_id
    assert stored["location"] == "Gole Ka Mandir"
    assert stored["location_normalized"] == "gole ka mandir"
    assert (stored["state_code"], stored["city_code"], stored["area_code"]) == (
        "mp", "mp-gwalior", "mp-gwalior-gole-ka-mandir")
    assert suggestion["abstained"] == 0
    assert ledger[0]["event_type"] == "suggested"

    officer = staff_user("operator", "Electricity Dept")
    request = configure_staff_location(officer, "mp", "mp-gwalior",
                                       "mp-gwalior-gole-ka-mandir", "Verified test scope")
    assert request["status"] == "pending"
    assert list_staff_cases(officer) == []

    admin = staff_user("reviewer", "Grievance Cell")
    approve_area_request(request["request_id"], admin["email"], "Approved synthetic Gwalior test scope")
    cases = list_staff_cases(officer)
    operator_cases = {case["id"]: case for case in cases}
    assert complaint_id in operator_cases and abstained_id in operator_cases
    assert operator_cases[complaint_id]["original_text"] == original
    assert operator_cases[complaint_id]["location"] == "Gole Ka Mandir"
    assert operator_cases[complaint_id]["language"] == "english"
    assert operator_cases[complaint_id]["channel"] == "portal"
    assert operator_cases[abstained_id]["uncertain"] is True

    # Abstained submissions remain visible in the human review queue, even
    # though they have no department assignment for an operator inbox.
    reviewer = staff_user("reviewer", "Grievance Cell")
    review_request = configure_staff_location(reviewer, "mp", "mp-gwalior",
                                              "mp-gwalior-gole-ka-mandir", "Verified test scope")
    approve_area_request(review_request["request_id"], admin["email"], "Approved synthetic Gwalior test scope")
    review_cases = {case["id"]: case for case in list_staff_cases(reviewer)}
    assert complaint_id in review_cases
    assert review_cases[complaint_id]["original_text"] == original
    assert review_cases[complaint_id]["location"] == "Gole Ka Mandir"
    assert abstained_id in review_cases
    assert review_cases[abstained_id]["original_text"] == abstained_text
    assert review_cases[abstained_id]["location"] == "Gole Ka Mandir"
    assert review_cases[abstained_id]["uncertain"] is True
    assert review_cases[abstained_id]["suggested_department"] is None
    assert operator_cases[complaint_id]["suggested_department"] == "Electricity Dept"


def test_public_form_resolves_normalized_english_and_hindi_area_aliases_without_rewriting_location(db):
    from pathlib import Path

    resident = auth.register_user("area-alias-resident@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    root = Path(__file__).resolve().parent.parent
    for location in ("  Gole   ka Mandir  ", "गोले का मंदिर"):
        app = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
        app.session_state["authenticated_user"] = resident
        app.run()
        app.selectbox(key="resident-complaint-state").select_index(1).run()
        app.selectbox(key="resident-complaint-city-mp").select_index(1).run()
        # Leave the structured area as unknown; exact normalized name/alias may
        # supply its known code while the original location value remains intact.
        app.selectbox(key="resident-complaint-area-mp-gwalior").select_index(0).run()
        app.text_area[0].set_value("There is a pothole here.")
        location_input = next(item for item in app.text_input if item.label == "Street or nearby landmark (optional)")
        location_input.set_value(location)
        app.form_submit_button[0].click().run()
        _confirm_public_preview(app)
        assert not app.exception
        case_id = app.session_state["last_public_submission"]["case_id"]
        stored = fetch_all("SELECT location,area_code,area_name,state_code,city_code FROM complaints WHERE case_id=?",
                           (case_id,))[0]
        assert stored["location"] == location
        assert stored["area_code"] == "mp-gwalior-gole-ka-mandir"
        assert stored["area_name"] == "Gole Ka Mandir"
        assert (stored["state_code"], stored["city_code"]) == ("mp", "mp-gwalior")


def test_citizen_confirms_and_can_correct_extracted_fields_before_any_database_write(db):
    from pathlib import Path
    import json

    resident = auth.register_user("confirm-fields@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "pages/10_Public_Portal.py",
                            default_timeout=30)
    app.session_state["authenticated_user"] = resident
    app.run()
    app.selectbox(key="resident-complaint-state").select_index(1).run()
    app.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    app.selectbox(key="resident-complaint-area-mp-gwalior").select_index(2).run()
    original = "the streetlight is not working near my home"
    app.text_area[0].set_value(original)
    app.form_submit_button[0].click().run()

    assert not app.exception
    assert "public_intake_preview" in app.session_state
    assert any("Review extracted details before submission" in item.value for item in app.subheader)
    assert fetch_all("SELECT id FROM complaints WHERE submitted_by=?", (resident["id"],)) == []
    app.selectbox(key="public-preview-issue").select_index(5).run()
    _confirm_public_preview(app)
    assert not app.exception

    case_id = app.session_state["last_public_submission"]["case_id"]
    complaint = fetch_all("SELECT * FROM complaints WHERE case_id=?", (case_id,))[0]
    suggestion = fetch_all("SELECT * FROM suggestions WHERE complaint_id=?", (complaint["id"],))[0]
    event = fetch_all("SELECT details FROM ledger WHERE complaint_id=?", (complaint["id"],))[0]
    assert complaint["original_text"] == original
    assert complaint["issue"] == "Road damage"
    assert json.loads(suggestion["top3"])[0]["dept"] == "Electricity Dept"
    details = json.loads(event["details"])
    assert details["extraction_corrections"]["issue"] == {
        "extracted": "Electricity", "confirmed": "Road damage"}


def test_public_submission_rolls_back_if_suggestion_or_ledger_write_fails(db):
    from core import intake, router_baseline

    rec = intake.process("streetlight is broken", "portal", "Gole Ka Mandir")
    rec.update({"case_id": "CASE-ROLLBACK", "state_code": "mp", "state_name": "Madhya Pradesh",
                "city_code": "mp-gwalior", "city_name": "Gwalior",
                "area_code": "mp-gwalior-gole-ka-mandir", "area_name": "Gole Ka Mandir"})
    db.execute("CREATE TRIGGER reject_suggestion BEFORE INSERT ON suggestions "
               "BEGIN SELECT RAISE(ABORT, 'simulated suggestion write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated suggestion write failure"):
        insert_public_submission(rec, router_baseline.route(rec["original_text"]))
    assert fetch_all("SELECT id FROM complaints WHERE case_id='CASE-ROLLBACK'") == []
    assert fetch_all("SELECT id FROM suggestions WHERE complaint_id NOT IN (SELECT id FROM complaints)") == []


def test_public_form_displays_error_and_saves_no_case_when_database_write_fails(db):
    from pathlib import Path

    resident = auth.register_user("failed-submit@example.test", "Resident", "Secure!River72",
                                  password_confirm="Secure!River72")
    db.execute("CREATE TRIGGER reject_suggestion BEFORE INSERT ON suggestions "
               "BEGIN SELECT RAISE(ABORT, 'simulated suggestion write failure'); END")
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "pages/10_Public_Portal.py",
                            default_timeout=30)
    app.session_state["authenticated_user"] = resident
    app.run()
    app.selectbox(key="resident-complaint-state").select_index(1).run()
    app.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    app.selectbox(key="resident-complaint-area-mp-gwalior").select_index(2).run()
    original = "the streetlight is not working in my area"
    app.text_area[0].set_value(original)
    app.form_submit_button[0].click().run()
    _confirm_public_preview(app)

    assert not app.exception
    assert any("could not be saved" in item.value for item in app.error)
    assert not any("Your complaint was submitted" in item.value for item in app.success)
    assert fetch_all("SELECT id FROM complaints WHERE submitted_by=? AND original_text=?",
                     (resident["id"], original)) == []


def test_location_normalization_is_separate_and_preserves_entered_value():
    from core.intake import process

    rec = process("Streetlight is broken", "portal", "  Gole   Ka Mandir  ")
    assert rec["location"] == "  Gole   Ka Mandir  "
    assert rec["location_normalized"] == "gole ka mandir"


def test_location_normalization_migration_backfills_existing_rows(tmp_path, monkeypatch):
    path = tmp_path / "pre-location-normalization.db"
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    conn = dbm.get_conn()
    try:
        conn.execute("DROP INDEX idx_complaints_location_normalized")
        conn.execute("ALTER TABLE complaints DROP COLUMN location_normalized")
        conn.execute("INSERT INTO complaints(case_id,original_text,language,location) "
                     "VALUES('CASE-OLD-LOCATION','old text','english','  Cafe\u0301   Square  ')")
        conn.commit()
    finally:
        conn.close()

    dbm.init_db()
    migrated = dbm.fetch_all(
        "SELECT location,location_normalized FROM complaints WHERE case_id='CASE-OLD-LOCATION'")[0]
    assert migrated == {"location": "  Cafe\u0301   Square  ", "location_normalized": "café square"}


def test_demo_geography_admin_is_an_active_registrable_reviewer(db):
    from core.geo import is_geography_admin

    email = "reviewer05@staff.nagriksetu-demo.example"
    rows = fetch_all("SELECT role,status FROM staff_directory WHERE staff_email=?", (email,))
    assert rows == [{"role": "reviewer", "status": "active"}]
    assert is_geography_admin(email)


def test_resident_cannot_create_review_request_for_someone_elses_case(db):
    owner = auth.register_user("owner@example.test", "Owner", "Secure!River72",
                               password_confirm="Secure!River72")
    other = auth.register_user("other@example.test", "Other", "Secure!River72",
                               password_confirm="Secure!River72")
    cid = _complaint("CASE-PRIVATE", owner["id"])
    from core.challenges import create_challenge
    with pytest.raises(PermissionError, match="only for their own"):
        create_challenge(cid, "citizen", "Please check this", actor_email=other["email"],
                         citizen_user_id=other["id"])


def test_government_email_is_written_as_review_ledger_actor(db, monkeypatch):
    staff = _staff_account("reviewer06@staff.nagriksetu-demo.example", "Staff")
    from core.geo import approve_area_request, request_area_assignment
    from core import geo
    geo_admins = "reviewer07@staff.nagriksetu-demo.example"
    monkeypatch.setenv("NYAYAFLOW_GEO_ADMIN_EMAILS", geo_admins)
    admin = _staff_account(geo_admins, "Admin")
    request_id = request_area_assignment(staff["id"], "mp", "mp-gwalior", "mp-gwalior-morar", staff["email"])
    approve_area_request(request_id, admin["email"], "Verified for test")
    complaint_id = insert_complaint({
        "case_id": "CASE-ACTOR", "original_text": "Streetlight is broken", "language": "english",
        "normalized_text": "streetlight is broken", "channel": "portal", "location": "Morar",
        "issue": "electricity", "requested_action": "repair", "missing_fields": "", "is_synthetic": 1,
        "state_code": "mp", "city_code": "mp-gwalior", "area_code": "mp-gwalior-morar",
    })
    submit_review(complaint_id, "reviewer", "accept", "Roads Dept", actor_email=staff["email"],
                  actor_user=staff)
    row = fetch_all("SELECT actor FROM ledger WHERE complaint_id=?", (complaint_id,))[0]
    assert row["actor"] == staff["email"]


def test_photo_validation_size_extension_and_exif_removal(db, monkeypatch, tmp_path):
    monkeypatch.setenv("NYAYAFLOW_ATTACHMENT_KEY", secrets.token_hex(32))
    monkeypatch.setattr("core.attachment.ATTACHMENT_DIR", tmp_path)
    reviewer = _staff_account("reviewer01@staff.nagriksetu-demo.example", "Photo Reviewer")
    monkeypatch.setenv("NYAYAFLOW_GEO_ADMIN_EMAILS", reviewer["email"])
    complaint_id = _complaint("CASE-PHOTO")

    bad_extension = io.BytesIO(b"not image")
    bad_extension.name = "proof.exe"
    with pytest.raises(ValueError, match="PNG, JPG"):
        save_attachment(complaint_id, bad_extension, "operator")

    oversized = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024))
    oversized.name = "large.png"
    with pytest.raises(ValueError, match="5 MB"):
        save_attachment(complaint_id, oversized, "operator")

    exif = Image.Exif()
    exif[271] = "Camera brand"
    image = io.BytesIO()
    Image.new("RGB", (3, 3), "red").save(image, format="PNG", exif=exif)
    image.name = "evidence.png"
    attachment_id = save_attachment(complaint_id, image, "reviewer", actor_user=reviewer)
    assert (tmp_path / attachment_id).is_file()
    clean = Image.open(io.BytesIO(read_attachment(
        fetch_all("SELECT id FROM attachments WHERE filename=?", (attachment_id,))[0]["id"], "reviewer",
        actor_user=reviewer)))
    assert not clean.getexif()
