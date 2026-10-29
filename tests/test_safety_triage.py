import json

import pytest


@pytest.mark.parametrize("text,expected", [
    ("Garbage has not been collected in Ward 12.", False),
    ("There is an open manhole in the middle of a busy road.", True),
    ("A fallen electric wire is sparking on the road.", True),
    ("The electric wire was repaired yesterday; there are no sparks now.", False),
    ("There is no fire or smoke.", False),
    ("The wall is cracking and pieces are falling near pedestrians.", True),
    ("I read about an open manhole in another city.", False),
    ("Ward 12 mein road par gaddha hai, streetlight band hai aur kachra nahi uthaya ja raha.", False),
    ("Wire se sparks nikal rahe hain aur bachche school ke paas se guzarte hain.", True),
    ("Khula manhole busy road ke beech mein hai.", True),
    ("The wire was repaired yesterday, but another live wire is sparking on a busy road.", True),
    ("The wire was repaired yesterday. Another live wire is sparking on a busy road.", True),
    ("No sparks from the exposed wire.", True),
    ("Something near the school may be dangerous, but I cannot tell what it is.", True),
])
def test_multilingual_context_aware_safety_triage(text, expected):
    from core.safety import assess_safety

    result = assess_safety(text)
    assert result["safety_flag"] is expected
    assert 0 <= result["risk_score"] <= 100
    if expected:
        assert result["requires_human_review"]
        if result["severity"] == "needs_human_verification":
            assert result["context_status"] == "unclear"
        else:
            assert result["rule_ids"]
            assert result["evidence"]
            assert result["severity"] in {"attention_recommended", "priority_safety_review", "urgent_review"}
    else:
        assert result["severity"] == "normal_queue"


def test_safety_evidence_uses_original_words_and_reports_configuration():
    from core.safety import assess_safety

    result = assess_safety("A FALLEN electric wire is sparking on the road!")
    assert "FALLEN electric wire" in result["evidence"]
    assert result["critical_override"]
    assert result["severity"] == "urgent_review"
    assert result["rule_version"] == "v1"
    assert "not validated" in result["threshold_status"]


def test_safety_review_override_is_reasoned_and_append_only(db, staff_user):
    from core.db import fetch_all, insert_complaint
    from core.safety import record_safety_decision
    from core.intake import process

    reviewer = staff_user("reviewer")
    complaint = process("A fallen electric wire is sparking on the road.")
    complaint["case_id"] = "CASE-SAFETY-REVIEW"
    complaint_id = insert_complaint(complaint)
    before = len(fetch_all("SELECT id FROM ledger WHERE complaint_id=?", (complaint_id,)))
    with pytest.raises(ValueError, match="reason"):
        record_safety_decision(complaint_id, reviewer, "false_positive", "")
    record_safety_decision(complaint_id, reviewer, "false_positive", "Reviewer visited and confirmed the wire was isolated.")
    events = fetch_all("SELECT event_type,actor,reason,details FROM ledger WHERE complaint_id=? ORDER BY id",
                       (complaint_id,))
    assert len(events) == before + 1
    assert events[-1]["event_type"] == "safety_review_false_positive"
    assert events[-1]["actor"] == reviewer["email"]
    details = json.loads(events[-1]["details"])
    assert details["system_assessment"]["severity"] == "urgent_review"
    assert details["previous_value"]["safety_flag"] is True
    assert details["updated_value"]["safety_flag"] is False
    record_safety_decision(complaint_id, reviewer, "change_severity",
                           "Reviewer confirms it remains a hazard but is no longer urgent.",
                           severity="priority_safety_review")
    latest = fetch_all("SELECT event_type,reason,details,created_at FROM ledger "
                       "WHERE complaint_id=? ORDER BY id", (complaint_id,))[-1]
    changed = json.loads(latest["details"])
    assert latest["event_type"] == "safety_review_change_severity"
    assert changed["previous_value"]["severity"] == "urgent_review"
    assert changed["updated_value"]["severity"] == "priority_safety_review"
    assert latest["created_at"] and latest["reason"]
    with pytest.raises(PermissionError):
        record_safety_decision(complaint_id, {"id": staff_user("operator")["id"]}, "resolved", "Checked")
