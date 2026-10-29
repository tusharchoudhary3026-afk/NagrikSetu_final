import json
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_reviewer_saves_versioned_rubric_and_rules_drive_attention_flags(db, staff_user):
    from core.db import fetch_all
    from core.safety import flag_safety, load_rubric, save_rubric_version

    reviewer = staff_user("reviewer")
    current = load_rubric()
    rules = [*current["rules"], {"id": "calm_sparking_wire", "label": "Possible electrical hazard",
                                  "terms": ["calm test hazard"]}]
    version = save_rubric_version(reviewer, rules, "Add a reviewed synthetic rubric test term.")
    assert version == current.get("version", 1) + 1
    saved = load_rubric()
    assert saved["version"] == version
    assert saved["status"] == "TEAM-PROPOSED - requires human review"
    assert flag_safety("calm test hazard") == {
        "flagged": True,
        "flags": [{"rule_id": "calm_sparking_wire", "label": "Possible electrical hazard",
                   "matched_text": ["calm test hazard"], "status": "HUMAN_REVIEW_REQUIRED"}],
        "rubric_status": "TEAM-PROPOSED - requires human review",
        "rubric_version": version,
        "message": "Safety flag is a request for human attention, not a risk or priority decision.",
    }
    record = fetch_all("SELECT version,saved_by,reason,rules_json FROM safety_rubric_versions WHERE version=?",
                       (version,))[0]
    assert record["saved_by"] == reviewer["email"]
    assert json.loads(record["rules_json"])[-1]["id"] == "calm_sparking_wire"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE safety_rubric_versions SET reason='changed' WHERE version=?", (version,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM safety_rubric_versions WHERE version=?", (version,))

    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = reviewer
    app.run()
    app.switch_page("pages/15_Model_Evaluation.py").run()
    assert not app.exception
    assert any(item.label == "Save new team-proposed rubric version" for item in app.button)


def test_rubric_version_requires_permission_reason_and_valid_rules(db, staff_user):
    from core.safety import load_rubric, save_rubric_version

    reviewer = staff_user("reviewer")
    operator = staff_user("operator", "Roads Dept")
    rules = load_rubric()["rules"]
    with pytest.raises(PermissionError):
        save_rubric_version(operator, rules, "Operator cannot edit policy.")
    with pytest.raises(ValueError, match="why this team-proposed"):
        save_rubric_version(reviewer, rules, "")
    with pytest.raises(ValueError, match="unique"):
        save_rubric_version(reviewer, [rules[0], rules[0]], "Duplicate rule IDs.")
