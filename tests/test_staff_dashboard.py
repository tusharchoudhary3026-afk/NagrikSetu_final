import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.db import execute, fetch_all, insert_complaint, insert_suggestion


def make_case(case_id, *, issue="Road damage", area="Ward 4", days_ago=0, synthetic=1, text=None):
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    cid = insert_complaint({
        "case_id": case_id, "original_text": text or f"Pothole near {area}", "language": "english",
        "normalized_text": (text or f"Pothole near {area}").lower(), "channel": "portal", "location": area,
        "issue": issue, "requested_action": None, "missing_fields": "", "is_synthetic": synthetic,
        "state_code": None, "city_code": None, "area_code": None,
    })
    execute("UPDATE complaints SET created_at=? WHERE id=?", (created, cid))
    return cid


def route_case(cid, department):
    insert_suggestion(cid, "baseline", [{"dept": department, "score": .8}],
                      {department: ["pothole"]}, .8, False)


def test_operator_dashboard_is_department_filtered_and_service_rejects_cross_department(db, staff_user):
    from core.staff_dashboard import list_staff_cases, perform_staff_action
    from core.geo import scoped_complaints, require_complaint_area_access

    officer = staff_user("operator", "Roads Dept")
    road_case = make_case("DASH-ROAD")
    sanitation_case = make_case("DASH-SANITATION", issue="Sanitation")
    route_case(road_case, "Roads Dept")
    route_case(sanitation_case, "Sanitation Dept")

    cases = list_staff_cases(officer)
    assert [case["id"] for case in cases] == [road_case]
    # A forged department in the session object is ignored; the database wins.
    officer["department"] = "Sanitation Dept"
    assert [case["id"] for case in list_staff_cases(officer)] == [road_case]
    assert [case["id"] for case in scoped_complaints(officer, include_unassigned=True)] == [road_case]
    with pytest.raises(PermissionError, match="assigned department"):
        require_complaint_area_access(officer, sanitation_case)
    with pytest.raises(PermissionError):
        perform_staff_action(officer, sanitation_case, "dispatch", note="Forged case id")


def test_dispatch_and_resolve_are_explicit_append_only_actions(db, staff_user):
    from core.staff_dashboard import perform_staff_action

    officer = staff_user("operator", "Roads Dept")
    cid = make_case("DASH-ACTIONS")
    route_case(cid, "Roads Dept")
    before = fetch_all("SELECT * FROM ledger WHERE complaint_id=?", (cid,))

    perform_staff_action(officer, cid, "dispatch", note="Field unit assigned to inspect pothole")
    perform_staff_action(officer, cid, "resolve", note="Repair completed; site checked by field unit")
    events = fetch_all("SELECT event_type,actor,reason,details FROM ledger WHERE complaint_id=? ORDER BY id", (cid,))
    assert [event["event_type"] for event in events] == ["dispatch", "resolve"]
    assert all(event["actor"] == officer["email"] for event in events)
    assert events[0]["reason"] and json.loads(events[1]["details"])["status"] == "resolved"
    assert before == []
    with pytest.raises(sqlite3.IntegrityError):
        execute("UPDATE ledger SET reason='changed' WHERE complaint_id=?", (cid,))


def test_transfer_requires_reason_and_changes_operator_visibility(db, staff_user):
    from core.staff_dashboard import list_staff_cases, perform_staff_action

    officer = staff_user("operator", "Roads Dept")
    reviewer = staff_user("reviewer", "Grievance Cell")
    cid = make_case("DASH-TRANSFER")
    route_case(cid, "Roads Dept")

    with pytest.raises(ValueError, match="reason"):
        perform_staff_action(officer, cid, "transfer", note="handoff", target_department="Drainage Dept")
    perform_staff_action(officer, cid, "transfer", note="Drainage inspection confirms blocked culvert",
                         reason_code="overlapping_ownership", target_department="Drainage Dept")

    assert list_staff_cases(officer) == []
    assert [case["id"] for case in list_staff_cases(reviewer)] == [cid]
    event = fetch_all("SELECT * FROM ledger WHERE complaint_id=?", (cid,))[0]
    assert event["event_type"] == "transfer"
    assert event["suggested_dept"] == "Roads Dept" and event["final_dept"] == "Drainage Dept"
    assert event["reason_code"] == "overlapping_ownership"
    details = json.loads(event["details"])
    assert details["prior_status"] == "new" and details["new_status"] == "pending"
    assert details["previous_department"] == "Roads Dept" and details["new_department"] == "Drainage Dept"


def test_repeated_transfer_bounce_is_warned_and_each_handoff_has_reason(db, staff_user):
    from core.staff_dashboard import perform_staff_action, get_staff_case_detail

    reviewer = staff_user("reviewer", "Grievance Cell")
    cid = make_case("DASH-BOUNCE")
    route_case(cid, "Roads Dept")
    perform_staff_action(reviewer, cid, "transfer", note="Drainage overlap review",
                         reason_code="overlapping_ownership", target_department="Drainage Dept")
    perform_staff_action(reviewer, cid, "transfer", note="Road damage is primary after joint review",
                         reason_code="overlapping_ownership", target_department="Roads Dept")
    events = fetch_all("SELECT reason,reason_code FROM ledger WHERE complaint_id=? ORDER BY id", (cid,))
    detail = get_staff_case_detail(reviewer, cid)
    assert all(event["reason"] and event["reason_code"] for event in events)
    assert detail["bounce_warning"]["pattern"] == "Roads Dept -> Drainage Dept -> Roads Dept"


def test_clarification_is_open_and_preserves_original_complaint(db, staff_user):
    from core.staff_dashboard import perform_staff_action, get_staff_case_detail, get_dashboard_summary

    officer = staff_user("operator", "Roads Dept")
    cid = make_case("DASH-CLARIFY")
    route_case(cid, "Roads Dept")
    original = fetch_all("SELECT original_text FROM complaints WHERE id=?", (cid,))[0]["original_text"]

    perform_staff_action(officer, cid, "clarify", note="Please identify which side of the road is affected")
    detail = get_staff_case_detail(officer, cid)
    assert detail["status"] == "awaiting clarification"
    assert detail["complaint"]["original_text"] == original
    assert len(detail["open_challenges"]) == 1
    assert fetch_all("SELECT event_type FROM ledger WHERE complaint_id=?", (cid,))[0]["event_type"] == "clarify"
    assert get_dashboard_summary(officer)["awaiting_clarification"] == 1


def test_dashboard_sla_is_labeled_demo_and_resolved_cases_are_not_overdue(db, staff_user):
    from core.staff_dashboard import list_staff_cases

    officer = staff_user("operator", "Roads Dept")
    old_case = make_case("DASH-OLD", days_ago=10)
    resolved = make_case("DASH-RESOLVED", days_ago=10)
    route_case(old_case, "Roads Dept")
    route_case(resolved, "Roads Dept")
    from core.staff_dashboard import perform_staff_action
    perform_staff_action(officer, resolved, "resolve", note="Work verified complete")

    rows = {row["id"]: row for row in list_staff_cases(officer)}
    assert rows[old_case]["sla"]["overdue"] is True
    assert rows[old_case]["sla"]["label"].startswith("Overdue")
    assert rows[old_case]["sla"]["assumption"]
    assert rows[resolved]["status"] == "resolved"
    assert rows[resolved]["sla"]["overdue"] is False


def test_hotspot_summary_is_scoped_to_officer_department_and_area(db, staff_user):
    from core.staff_dashboard import get_ward_hotspots

    officer = staff_user("operator", "Roads Dept")
    road = make_case("HOT-ROAD", issue="Pothole", area="Ward 4", days_ago=1)
    sanitation = make_case("HOT-SANITATION", issue="Garbage", area="Ward 4", days_ago=1)
    route_case(road, "Roads Dept")
    route_case(sanitation, "Sanitation Dept")

    results = get_ward_hotspots(officer)
    assert results and all(row["issue"] == "Pothole" for row in results)
    assert results[0]["location"] == "Ward 4"
    assert results[0]["data_label"] == "SIMULATED / SYNTHETIC"


def test_auditor_is_read_only_and_public_users_cannot_use_staff_dashboard(db, staff_user):
    from core.staff_dashboard import list_staff_cases, perform_staff_action
    from core.auth import register_user

    auditor = staff_user("auditor", "Grievance Cell")
    resident = register_user("dashboard-resident@example.test", "Resident", "Safe!River72",
                             password_confirm="Safe!River72")
    cid = make_case("DASH-READONLY")
    route_case(cid, "Roads Dept")
    assert [case["id"] for case in list_staff_cases(auditor)] == [cid]
    with pytest.raises(PermissionError, match="read-only"):
        perform_staff_action(auditor, cid, "dispatch", note="Should be blocked")
    with pytest.raises(PermissionError):
        list_staff_cases(resident)


def test_staff_department_is_revalidated_against_directory_on_each_dashboard_load(db, staff_user):
    from core.staff_dashboard import list_staff_cases

    officer = staff_user("operator", "Roads Dept")
    execute("UPDATE users SET department='Sanitation Dept' WHERE id=?", (officer["id"],))
    with pytest.raises(PermissionError, match="active"):
        list_staff_cases(officer)


def test_staff_login_lands_on_operations_dashboard_not_public_submission(db, staff_user):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    operator = staff_user("operator", "Roads Dept")
    case_id = make_case("DASH-APPTEST")
    route_case(case_id, "Roads Dept")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = operator
    app.run()
    assert not app.exception
    rendered = " ".join(item.value for item in list(app.title) + list(app.subheader) + list(app.markdown))
    assert "Complaint operations" in rendered
    assert any(item.label == "New" for item in app.metric)
    assert any("Officer inbox" in item.value for item in app.subheader)
    assert any("DASH-APPTEST" in item.value for item in app.subheader)
    assert not any(link.label in {"Report a problem", "Submit & track"} for link in app.page_link)


def test_case_detail_includes_ownership_source_safety_and_read_only_timeline(db, staff_user):
    from core.staff_dashboard import get_staff_case_detail

    reviewer = staff_user("reviewer", "Grievance Cell")
    cid = make_case("DASH-OVERLAP", issue="Waterlogging", area="Ward 4",
                    text="Waterlogging on road near Ward 4")
    insert_suggestion(cid, "baseline", [
        {"dept": "Drainage Dept", "score": .8}, {"dept": "Roads Dept", "score": .5}],
        {"Drainage Dept": ["waterlogging"], "Roads Dept": ["road"]}, .8, False)
    detail = get_staff_case_detail(reviewer, cid)
    assert detail["ownership_conflicts"]
    assert detail["ownership_conflicts"][0]["source"]
    assert detail["timeline"][0]["event_type"] == "submitted"
    assert any(event["event_type"] == "routing_suggestion" for event in detail["timeline"])


def test_operator_duplicate_candidates_do_not_query_other_departments(db, staff_user):
    from core.staff_dashboard import get_staff_case_detail

    officer = staff_user("operator", "Roads Dept")
    road = make_case("DASH-DUP-ROAD", text="Pothole near the Ward 4 school")
    sanitation = make_case("DASH-DUP-SANITATION", issue="Sanitation",
                           text="Pothole near the Ward 4 school")
    route_case(road, "Roads Dept")
    route_case(sanitation, "Sanitation Dept")

    detail = get_staff_case_detail(officer, road)
    assert all(candidate["case_id"] != "DASH-DUP-SANITATION" for candidate in detail["duplicates"])


def test_uncertain_languages_compound_duplicate_and_missing_area_remain_visible(db, staff_user):
    from core import intake, router_baseline
    from core.db import insert_public_submission
    from core.geo import configure_staff_location
    from core.staff_dashboard import list_staff_cases

    reviewer = staff_user("reviewer", "Grievance Cell")
    configure_staff_location(reviewer, "mp", "mp-gwalior", "mp-gwalior-gole-ka-mandir")

    texts = [
        "The streetlight is not working in my area",  # English
        "मेरे इलाके में स्ट्रीटलाइट काम नहीं कर रही है",  # Hindi
        "mere area mein streetlight kaam nahi kar rahi",  # Hinglish
        "gibberish xyz 123",  # abstained / manual review
        "streetlight is off and garbage is piled up",  # compound issue
        "The streetlight is not working in my area",  # duplicate-looking report remains separate
    ]
    expected_ids = []
    for index, text in enumerate(texts):
        rec = intake.process(text, "portal", "Gole Ka Mandir")
        rec.update({"case_id": f"CASE-VARIANT-{index}", "state_code": "mp",
                    "state_name": "Madhya Pradesh", "city_code": "mp-gwalior",
                    "city_name": "Gwalior", "area_code": "mp-gwalior-gole-ka-mandir",
                    "area_name": "Gole Ka Mandir"})
        expected_ids.append(insert_public_submission(rec, router_baseline.route(text)))

    no_area = intake.process("unclassified report with no location", "portal", "")
    no_area["case_id"] = "CASE-VARIANT-NO-AREA"
    no_area_id = insert_public_submission(no_area, router_baseline.route(no_area["original_text"]))
    no_suggestion_id = insert_complaint({
        "case_id": "CASE-VARIANT-NO-SUGGESTION", "original_text": "Saved complaint with no routing output",
        "language": "uncertain", "normalized_text": "saved complaint with no routing output",
        "channel": "portal", "location": "Gole Ka Mandir", "issue": "unknown", "is_synthetic": 1,
        "state_code": "mp", "state_name": "Madhya Pradesh", "city_code": "mp-gwalior",
        "city_name": "Gwalior", "area_code": "mp-gwalior-gole-ka-mandir",
        "area_name": "Gole Ka Mandir",
    })

    cases = list_staff_cases(reviewer)
    visible = {case["id"]: case for case in cases}
    assert set(expected_ids + [no_area_id, no_suggestion_id]).issubset(visible)
    assert visible[expected_ids[1]]["original_text"] == texts[1]
    assert visible[expected_ids[1]]["language"] == "hindi"
    assert visible[expected_ids[2]]["language"] == "hinglish"
    assert visible[expected_ids[3]]["uncertain"] is True
    assert visible[expected_ids[3]]["suggested_department"] is None
    assert visible[expected_ids[3]]["status"] == "new"
    assert visible[no_area_id]["location"] == "unknown"
    assert visible[no_suggestion_id]["uncertain"] is True
    assert visible[no_suggestion_id]["suggested_department"] is None
    # Similar reports remain separate records; this dashboard never auto-merges.
    assert visible[expected_ids[0]]["id"] != visible[expected_ids[5]]["id"]
