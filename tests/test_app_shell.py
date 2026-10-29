from pathlib import Path

from streamlit.testing.v1 import AppTest
from core.auth import begin_staff_registration, register_user, verify_staff_registration
from core.db import execute


def _staff_account(email, name="Test Staff", role="reviewer"):
    execute("INSERT OR IGNORE INTO staff_directory(staff_email,full_name,role,department,status,registered,tag) "
            "VALUES(?,?,?,'Grievance Cell','active',0,'SYNTHETIC')", (email, name, role))
    flow = begin_staff_registration(email, name, "Safe!Green2026", "Safe!Green2026")
    return verify_staff_registration(email, flow["simulated_code"])


def test_logged_out_app_shell_keeps_navigation_registered_and_shows_login(monkeypatch, db):
    monkeypatch.delenv("NYAYAFLOW_SETUP_TOKEN", raising=False)
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=20).run()
    assert not app.exception
    assert any(element.label == "Your name" for element in app.text_input)
    assert not any("Sign in to access this page" in element.value for element in app.error)


def test_relative_database_override_resolves_from_project_root():
    from core.config import ROOT, resolve_db_path

    assert resolve_db_path("data/local.sqlite3") == ROOT / "data" / "local.sqlite3"


def test_opening_protected_file_page_without_session_shows_sign_in_link(monkeypatch, db):
    monkeypatch.delenv("NYAYAFLOW_SETUP_TOKEN", raising=False)
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=20).run()
    app.switch_page("pages/5_Relabel.py").run()
    assert not app.exception
    assert any("requires a signed-in account" in item.value for item in app.info)
    assert any(link.label == "Go to NagrikSetu sign in" for link in app.page_link)


def test_reviewer_can_open_the_role_filtered_navigation(monkeypatch, db):
    account = _staff_account("reviewer01@staff.nagriksetu-demo.example")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=20)
    app.session_state["authenticated_user"] = account
    app.run()
    assert not app.exception
    assert any("NagrikSetu" in item.value for item in app.markdown)
    assert any("Reviewer" in item.value for item in app.markdown)
    assert sum(item.value.count("Reviewer") for item in app.markdown) == 1
    assert len(app.sidebar.get("caption")) == 0


def test_demo_staff_home_shows_preapproved_city_assignment_without_warning(monkeypatch, db):
    monkeypatch.setenv("NYAYAFLOW_SEED_DEMO_STAFF", "1")
    account = _staff_account("reviewer01@staff.nagriksetu-demo.example", "Arjun Demo", "reviewer")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = account
    app.run()
    assert not app.exception
    assert any("DEMO ACCOUNT — pre-approved synthetic assignment" in item.value for item in app.info)
    assert not any("No service area is verified" in item.value for item in app.warning)
    from core.geo import staff_location_snapshot
    assert staff_location_snapshot(account)["assigned"][0]["area_name"] == "All areas (city-wide)"


def test_non_demo_staff_can_self_select_area_without_admin_approval(db):
    account = _staff_account("browse-area@staff.example.test", "Browse Staff", "reviewer")
    from core.db import insert_public_submission
    from core.geo import staff_location_snapshot
    from core.staff_dashboard import list_staff_cases

    complaint_id = insert_public_submission({
        "case_id": "CASE-SELF-SELECT-AREA", "original_text": "streetlight is not working",
        "language": "english", "normalized_text": "streetlight is not working", "channel": "portal",
        "location": "Gole Ka Mandir", "issue": "Electricity", "is_synthetic": 1,
        "state_code": "mp", "state_name": "Madhya Pradesh", "city_code": "mp-gwalior",
        "city_name": "Gwalior", "area_code": "mp-gwalior-gole-ka-mandir", "area_name": "Gole Ka Mandir",
    }, {"top3": [{"dept": "Electricity Dept", "score": 0.9}], "evidence": {"matched": ["streetlight"]},
       "confidence": 0.9, "abstained": False})
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = account
    app.run()
    assert not app.exception
    assert any("Select a service area below" in item.value for item in app.info)
    app.selectbox(key="staff-area-select-state").select_index(0).run()
    app.selectbox(key="staff-area-select-city-mp").select_index(0).run()
    app.selectbox(key="staff-area-select-area-mp-gwalior").select_index(1).run()
    next(item for item in app.button if item.label == "Show complaints from this location").click().run()

    assert not app.exception
    assert any(row["area_code"] == "mp-gwalior-gole-ka-mandir"
               for row in staff_location_snapshot(account)["assigned"])
    assert staff_location_snapshot(account)["pending"] is None
    assert [row["case_id"] for row in list_staff_cases(account)] == ["CASE-SELF-SELECT-AREA"]
    assert any("CASE-SELF-SELECT-AREA" in item.value for item in app.subheader)


def test_each_demo_staff_role_sees_public_case_in_the_streamlit_staff_view(monkeypatch, db):
    monkeypatch.setenv("NYAYAFLOW_SEED_DEMO_STAFF", "1")
    import json
    from core.db import fetch_all
    accounts = {
        "operator01": _staff_account("operator01@staff.nagriksetu-demo.example", "Aarav Demo", "operator"),
        "reviewer01": _staff_account("reviewer01@staff.nagriksetu-demo.example", "Arjun Demo", "reviewer"),
        "auditor01": _staff_account("auditor01@staff.nagriksetu-demo.example", "Vihaan Demo", "auditor"),
    }
    root = Path(__file__).resolve().parent.parent
    resident = register_user("demo-staff-ui-resident@example.test", "Demo Resident", "Safe!Green2026",
                             password_confirm="Safe!Green2026")
    portal = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    portal.session_state["authenticated_user"] = resident
    portal.run()
    portal.selectbox(key="resident-complaint-state").select_index(1).run()
    portal.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    portal.selectbox(key="resident-complaint-area-mp-gwalior").select_index(2).run()
    original = "the streetlight is not working in my area"
    portal.text_area[0].set_value(original)
    portal.form_submit_button[0].click().run()
    portal.checkbox(key="public-preview-confirmed").set_value(True).run()
    portal.button(key="public-preview-submit").click().run()
    assert not portal.exception
    case_id = portal.session_state["last_public_submission"]["case_id"]
    complaint = fetch_all("SELECT * FROM complaints WHERE case_id=?", (case_id,))[0]
    complaint_id = complaint["id"]
    assert complaint["original_text"] == original
    top3 = json.loads(fetch_all("SELECT top3 FROM suggestions WHERE complaint_id=?", (complaint_id,))[0]["top3"])
    assert any(row["dept"] == "Electricity Dept" for row in top3)
    assert complaint["location"] == "Gole Ka Mandir"

    for account in accounts.values():
        app = AppTest.from_file(root / "app.py", default_timeout=30)
        app.run()
        assert not app.exception
        next(item for item in app.text_input if item.label == "Email or mobile number").set_value(account["email"])
        next(item for item in app.text_input if item.label == "Password").set_value("Safe!Green2026")
        next(item for item in app.form_submit_button if item.label == "Sign in").click()
        app.run()
        assert not app.exception
        assert app.session_state["authenticated_user"]["id"] == account["id"]
        assert any("DEMO ACCOUNT — pre-approved synthetic assignment" in item.value for item in app.info)
        assert any(f"Case review · {case_id}" in item.value for item in app.subheader)
        from core.staff_dashboard import list_staff_cases
        cases = {item["id"]: item for item in list_staff_cases(account)}
        assert complaint_id in cases
        assert cases[complaint_id]["original_text"] == original
        assert cases[complaint_id]["location"] == "Gole Ka Mandir"


def test_every_page_starts_headlessly_with_a_permitted_session(db):
    root = Path(__file__).resolve().parent.parent
    public = register_user("resident@app-test.example", "Test Resident", "Safe!Green2026",
                           password_confirm="Safe!Green2026")
    reviewer = _staff_account("reviewer01@staff.nagriksetu-demo.example")

    for page in sorted((root / "pages").glob("*.py")):
        account = public if page.name in {"10_Public_Portal.py", "8_Request_Review.py"} else reviewer
        app = AppTest.from_file(root / "app.py", default_timeout=30)
        app.session_state["authenticated_user"] = account
        app.run()
        app.switch_page(f"pages/{page.name}").run()
        assert not app.exception, f"{page.name} raised: {[error.message for error in app.exception]}"
