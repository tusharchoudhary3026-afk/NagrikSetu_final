import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def _case(db):
    from core.db import insert_public_submission

    return insert_public_submission({
        "case_id": "CASE-INTAKE-CORRECTION", "original_text": "Streetlight is broken at Gole Ka Mandir.",
        "language": "english", "normalized_text": "streetlight broken at gole ka mandir",
        "channel": "portal", "location": "Gole Ka Mandir", "issue": "Electricity",
        "requested_action": None, "missing_fields": "", "is_synthetic": 1,
        "state_code": "mp", "state_name": "Madhya Pradesh", "city_code": "mp-gwalior",
        "city_name": "Gwalior", "area_code": "mp-gwalior-gole-ka-mandir", "area_name": "Gole Ka Mandir",
    }, {"top3": [{"dept": "Electricity Dept", "score": 0.9}],
        "evidence": {"Electricity Dept": ["streetlight"]}, "confidence": 0.9, "abstained": False})


def _approve_gole(user, admin):
    from core.geo import approve_area_request, configure_staff_location

    request = configure_staff_location(user, "mp", "mp-gwalior", "mp-gwalior-gole-ka-mandir",
                                       "Gole Ka Mandir service assignment")
    approve_area_request(request["request_id"], admin["email"], "Synthetic test grant")


def test_reviewer_corrects_extracted_fields_in_staff_ui_with_append_only_provenance(db, staff_user):
    from core.db import fetch_all

    reviewer = staff_user("reviewer")
    admin = staff_user("reviewer")
    _approve_gole(reviewer, admin)
    complaint_id = _case(db)
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = reviewer
    app.run()
    assert not app.exception
    assert any("Case review · CASE-INTAKE-CORRECTION" in item.value for item in app.subheader)
    app.selectbox(key=f"extract-issue-{complaint_id}").select_index(5).run()
    app.text_area(key=f"extract-reason-{complaint_id}").set_value("The resident confirmed this is road damage.").run()
    next(item for item in app.button if item.label == "Save extraction correction").click().run()
    assert not app.exception

    complaint = fetch_all("SELECT original_text,issue,location,requested_action FROM complaints WHERE id=?",
                          (complaint_id,))[0]
    assert complaint == {
        "original_text": "Streetlight is broken at Gole Ka Mandir.",
        "issue": "Road damage", "location": "Gole Ka Mandir", "requested_action": None}
    event = fetch_all("SELECT event_type,actor,reason,reason_code,details FROM ledger "
                      "WHERE complaint_id=? ORDER BY id DESC LIMIT 1", (complaint_id,))[0]
    assert event["event_type"] == "extraction_corrected"
    assert event["actor"] == reviewer["email"]
    assert event["reason"] == "The resident confirmed this is road damage."
    assert event["reason_code"] == "wrong_category"
    assert json.loads(event["details"])["before"]["issue"] == "Electricity"
    assert json.loads(event["details"])["after"]["issue"] == "Road damage"


def test_extraction_correction_requires_reason_and_review_permission(db, staff_user):
    from core.db import fetch_all
    from core.intake_corrections import correct_intake_fields

    reviewer = staff_user("reviewer")
    admin = staff_user("reviewer")
    _approve_gole(reviewer, admin)
    complaint_id = _case(db)
    with pytest.raises(ValueError, match="correction reason"):
        correct_intake_fields(reviewer, complaint_id, {"issue": "Road damage"}, "", "wrong_category")

    operator = staff_user("operator", "Roads Dept")
    with pytest.raises(PermissionError):
        correct_intake_fields(operator, complaint_id, {"issue": "Road damage"},
                              "Corrected by operator", "wrong_category")
    assert fetch_all("SELECT id FROM ledger WHERE complaint_id=? AND event_type='extraction_corrected'",
                     (complaint_id,)) == []
