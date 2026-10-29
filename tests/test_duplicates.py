from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from core import duplicates
from core.db import execute, fetch_all, insert_complaint
from core.analytics import get_transfer_pattern_summary
from core.ledger import log_event


def _complaint(text, case_id, *, location="unknown", issue="unknown", created_at=None,
               parent_id=None):
    payload = {
        "case_id": case_id, "parent_id": parent_id, "original_text": text,
        "normalized_text": text.casefold(), "language": "english", "channel": "portal",
        "location": location, "issue": issue, "requested_action": None,
        "missing_fields": "", "is_synthetic": 1,
    }
    cid = insert_complaint(payload)
    if created_at:
        execute("UPDATE complaints SET created_at=? WHERE id=?", (created_at, cid))
    return cid


def test_grounded_evidence_only_returns_shared_source_terms():
    evidence = duplicates.grounded_evidence(
        "streetlight broken by park", "streetlight stopped working by park",
        "electricity", "electricity", "Ward 12 Park Road", "Ward 12 Park Road")
    assert evidence["shared_terms"] == ["park", "streetlight"]
    assert evidence["shared_issue_labels"] == ["electricity"]
    assert "invented" not in evidence["shared_terms"]


def test_tfidf_evidence_and_top_level_guarded_candidates_remain_visible(db):
    first = _complaint("Streetlight broken near Ward 12 park", "DUP-A", location="Ward 12 Park Road",
                       issue="electricity")
    child = _complaint("Streetlight broken", "DUP-A-1", location="Ward 12 Park Road",
                       issue="electricity", parent_id=first)
    second = _complaint("Streetlight at Ward 12 park is out", "DUP-B", location="Ward 12 Park Road",
                        issue="electricity")
    distinct_issue = _complaint("Drain blocked near Ward 12 park", "DUP-C",
                                location="Ward 12 Park Road", issue="drainage")
    rows = duplicates.find_candidates(first, limit=10)
    ids = [row["complaint_id"] for row in rows]
    assert second in ids and child not in ids
    match = next(row for row in rows if row["complaint_id"] == second)
    assert match["text_sim"] > 0 and match["shared_issue_labels"] == ["electricity"]
    guarded = next(row for row in rows if row["complaint_id"] == distinct_issue)
    assert "same place, different issue" in guarded["guard_flags"]


def test_compare_pair_unknown_location_has_no_location_bonus_and_old_time_is_guarded():
    unknown = duplicates.compare_pair("pothole on road", "pothole on road",
                                      "unknown", "unknown", "2026-01-01", "2026-01-01",
                                      "roads", "roads")
    same = duplicates.compare_pair("pothole on road", "pothole on road",
                                   "Station Road", "Station Road", "2026-01-01", "2026-01-01",
                                   "roads", "roads")
    old = duplicates.compare_pair("pothole on road", "pothole on road",
                                  "Station Road", "Station Road", "2025-01-01", "2026-01-01",
                                  "roads", "roads")
    assert unknown["location"] == "unknown" and unknown["score"] < same["score"]
    assert old["time_difference_days"] > 7 and "outside configured time window" in old["conflicts"]


def test_confirmation_and_rejection_append_reasoned_events_for_both_cases(db, staff_user):
    reviewer = staff_user("reviewer")
    first = _complaint("Pothole by Station Road", "DUP-EVENT-A", location="Station Road", issue="roads")
    second = _complaint("Pothole by Station Road", "DUP-EVENT-B", location="Station Road", issue="roads")
    duplicates.record_decision(first, second, "same_incident", "reviewer", "Same pothole site",
                               actor_email=reviewer["email"], reason_code="same_incident", actor_user=reviewer)
    for cid in (first, second):
        assert {row["event_type"] for row in fetch_all("SELECT event_type FROM ledger WHERE complaint_id=?", (cid,))} >= {
            "duplicate_confirmed", "duplicate_linked"}
    initial_links = fetch_all("SELECT complaint_id,candidate_complaint_id,active FROM incident_links")
    assert initial_links == [{"complaint_id": min(first, second),
                              "candidate_complaint_id": max(first, second), "active": 1}]
    duplicates.record_decision(second, first, "same_incident", "reviewer", "Confirmed same site again",
                               actor_email=reviewer["email"], reason_code="same_incident", actor_user=reviewer)
    assert len(fetch_all("SELECT * FROM incident_links WHERE active=1")) == 1
    before = fetch_all("SELECT original_text FROM complaints WHERE id IN (?,?) ORDER BY id", (first, second))
    duplicates.record_decision(first, second, "unlink", "reviewer", "Separate events after inspection",
                               actor_email=reviewer["email"], reason_code="reopen_after_review", actor_user=reviewer)
    duplicates.record_decision(first, second, "different", "reviewer", "Different incidents",
                               actor_email=reviewer["email"], reason_code="different_incident", actor_user=reviewer)
    for cid in (first, second):
        events = {row["event_type"] for row in fetch_all("SELECT event_type FROM ledger WHERE complaint_id=?", (cid,))}
        assert {"duplicate_unlinked", "duplicate_rejected"} <= events
    after = fetch_all("SELECT original_text FROM complaints WHERE id IN (?,?) ORDER BY id", (first, second))
    assert before == after
    assert len(fetch_all("SELECT * FROM incident_links")) == 2


def test_public_user_is_blocked_from_duplicate_page(db, monkeypatch):
    from core.auth import register_user, start_session
    account = register_user("resident@example.test", "Resident", "Safe!Green2026",
                            password_confirm="Safe!Green2026")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    start_session(app.session_state, account)
    app.run()
    app.switch_page("pages/9_Duplicates.py").run()
    assert not app.exception
    assert any("This page isn&#x27;t available to your account" in item.value for item in app.markdown)
    assert not any("Ranked candidates" in item.value for item in app.markdown)


def test_reviewer_can_open_duplicate_page(db, staff_user):
    from core.auth import start_session
    reviewer = staff_user("reviewer")
    _complaint("Streetlight is broken at Ward 12 park", "DUP-PAGE-A",
               location="Ward 12 park", issue="electricity")
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    start_session(app.session_state, reviewer)
    app.run()
    app.switch_page("pages/9_Duplicates.py").run()
    assert not app.exception
    assert any(element.value == "Ranked candidates" for element in app.subheader)


def test_transfer_pattern_summary_reports_case_denominators(db):
    bounce = _complaint("Pothole on road", "DUP-BOUNCE", location="Station Road", issue="roads")
    repeat = _complaint("Drain blocked", "DUP-REPEAT", location="Morar", issue="drainage")
    _complaint("Streetlight broken", "DUP-NONE", location="Hazira", issue="electricity")
    log_event(bounce, "transfer", "reviewer", suggested_dept="Roads Dept", final_dept="Drainage Dept",
              reason="Possible overlap", reason_code="overlapping_ownership")
    log_event(bounce, "transfer", "reviewer", suggested_dept="Drainage Dept", final_dept="Roads Dept",
              reason="Reviewed ownership", reason_code="other")
    log_event(repeat, "transfer", "reviewer", suggested_dept="Roads Dept", final_dept="Drainage Dept",
              reason="Reviewed", reason_code="other")
    log_event(repeat, "transfer", "reviewer", suggested_dept="Drainage Dept", final_dept="Water Supply Dept",
              reason="Reviewed again", reason_code="other")
    summary = get_transfer_pattern_summary()
    assert summary["total_cases"] == 3 and summary["cases_with_transfers"] == 2
    assert summary["A_to_B_to_A"] == {"count": 1, "denominator": 2}
    assert summary["repeat_reassignment"] == {"count": 2, "denominator": 2}


def test_duplicate_decision_reason_is_required_by_database_trigger(db):
    cid = _complaint("Pothole by Station Road", "DUP-DB-REASON", location="Station Road", issue="roads")
    with pytest.raises(sqlite3.IntegrityError, match="duplicate decisions require a reason"):
        execute("INSERT INTO ledger (complaint_id,event_type,actor) VALUES (?, 'duplicate_confirmed','staff')",
                (cid,))
