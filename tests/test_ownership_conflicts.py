from core import ownership, router_baseline, splitter
from core.suggestions import selected_department


def test_damaged_pipeline_suggests_both_owners_and_abstains_for_manual_review():
    text = "pipeline is damaged in ward 7"
    route = router_baseline.route(text)
    departments = {item["dept"] for item in route["top3"]}

    assert {"Water Supply Dept", "Drainage Dept"} <= departments
    assert route["abstained"] is True
    assert selected_department(route) is None
    assert route["evidence"]["Water Supply Dept"]
    assert route["evidence"]["Drainage Dept"]
    conflicts = ownership.check_suggestions(list(departments), text)
    assert len(conflicts) == 1
    assert conflicts[0]["verification"] == "ASSUMPTION"
    assert conflicts[0]["source"]


def test_pipe_ambiguity_cues_are_narrow_and_do_not_flag_ordinary_water_reports():
    clear_water = router_baseline.route("Water supply pressure is low in ward 7")
    assert clear_water["abstained"] is False
    assert selected_department(clear_water) == "Water Supply Dept"
    assert not ownership.route_conflicts("Water supply pressure is low in ward 7")
    assert not ownership.route_conflicts("The pipeline is working after repair")


def test_ambiguous_pipe_and_clear_garbage_issue_stay_compound_with_conflict_manual_review():
    text = "pipeline is damaged and garbage rakha hai boht sara waste in ward 7"
    proposal = splitter.propose_split(text)

    assert proposal["is_compound"] is True
    assert len(proposal["parts"]) == 2
    pipe, garbage = proposal["parts"]
    assert pipe["issue_evidence"] and pipe["ownership_conflicts"]
    assert pipe["suggestion"]["abstained"] is True
    assert selected_department(pipe["suggestion"]) is None
    assert garbage["department"] == "Sanitation Dept"
    assert garbage["suggestion"]["abstained"] is False


def test_conflicted_case_is_visible_in_staff_manual_queue(db, staff_user):
    from core.db import insert_complaint, insert_suggestion, fetch_all
    from core.intake import process
    from core.staff_dashboard import list_staff_cases

    text = "pipeline is damaged in ward 7"
    rec = process(text, location_hint="Ward 7")
    rec.update({"case_id": "OWNERSHIP-PIPE-REVIEW"})
    complaint_id = insert_complaint(rec)
    route = router_baseline.route(text)
    insert_suggestion(complaint_id, "baseline", route["top3"], route["evidence"],
                      route["confidence"], route["abstained"])

    officer = staff_user("operator", "Water Supply Dept")
    case = next(row for row in list_staff_cases(officer) if row["id"] == complaint_id)
    assert case["uncertain"] is True
    assert case["current_department"] is None
    assert case["ownership_conflicts"]
    assert fetch_all("SELECT original_text FROM complaints WHERE id=?", (complaint_id,))[0]["original_text"] == text


def test_public_compound_preview_explains_contested_pipeline_review(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import register_user

    resident = register_user("ownership-preview@example.test", "Preview Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    root = Path(__file__).resolve().parent.parent
    portal = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    portal.session_state["authenticated_user"] = resident
    portal.run()
    portal.selectbox(key="resident-complaint-state").select_index(1).run()
    portal.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    portal.selectbox(key="resident-complaint-area-mp-gwalior").select_index(1).run()
    portal.text_area[0].set_value("pipeline is damaged and garbage is piled up in ward 7")
    portal.form_submit_button[0].click().run()

    assert not portal.exception
    assert any("Possible separate issues" in item.value for item in portal.subheader)
    assert any("manual staff review required" in item.value for item in portal.warning)
