"""Reasoned, append-only corrections to extracted complaint fields."""
import json

from .auth import authorize_user_action
from .config import REASON_CODES
from .db import get_conn
from .geo import authorize_staff_complaint
from .intake import ISSUE_CATEGORIES, normalize_location
from .staff_dashboard import _authorized_case

ALLOWED_FIELDS = {"issue", "location", "requested_action"}
ISSUES = {name for name, _ in ISSUE_CATEGORIES}
ACTIONS = {"repair", "clean", "clear", "inspect"}


def correct_intake_fields(user: dict, complaint_id: int, changes: dict,
                          reason: str, reason_code: str) -> dict:
    """Save corrections without changing the complaint's original text."""
    if not isinstance(changes, dict) or not changes or set(changes) - ALLOWED_FIELDS:
        raise ValueError("Choose at least one supported extracted field to correct.")
    actor, case = _authorized_case(user, complaint_id, writing=True)
    authorize_user_action(actor["id"], "can_review")
    reason = (reason or "").strip()
    if not reason or len(reason) > 1000 or any(ord(char) < 32 and char not in "\t\r\n" for char in reason):
        raise ValueError("Enter a correction reason of 1 to 1000 characters.")
    if reason_code not in REASON_CODES:
        raise ValueError("Choose a correction reason code.")

    clean = {}
    if "issue" in changes:
        issue = changes["issue"]
        if issue not in ISSUES and issue not in {None, "unknown"}:
            raise ValueError("Choose a supported issue category or unknown.")
        clean["issue"] = None if issue in {None, "unknown"} else issue
    if "location" in changes:
        location = changes["location"]
        if not isinstance(location, str) or len(location) > 250:
            raise ValueError("Location text must be 250 characters or fewer.")
        clean["location"] = location if location.strip() else "unknown"
        clean["location_normalized"] = normalize_location(clean["location"])
    if "requested_action" in changes:
        action = changes["requested_action"]
        if action not in ACTIONS and action not in {None, "unspecified"}:
            raise ValueError("Choose a supported requested action or unspecified.")
        clean["requested_action"] = None if action in {None, "unspecified"} else action
    if "location" in clean:
        # Location normalization is a separate matching value, never a replacement for entered text.
        clean["location_normalized"] = normalize_location(clean["location"])

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        persisted = conn.execute("SELECT active,role,department FROM users WHERE id=?", (actor["id"],)).fetchone()
        if not persisted or not persisted["active"] or persisted["role"] != actor["role"]:
            raise PermissionError("The staff account changed. Sign in again before correcting this case.")
        authorize_staff_complaint(actor["role"], complaint_id, actor_user=actor)
        current = conn.execute(
            "SELECT issue,location,location_normalized,requested_action,missing_fields FROM complaints WHERE id=?",
            (complaint_id,)).fetchone()
        if not current:
            raise ValueError("Complaint not found.")
        latest = conn.execute("SELECT id FROM ledger WHERE complaint_id=? ORDER BY id DESC LIMIT 1",
                              (complaint_id,)).fetchone()
        expected = case["history"][-1]["id"] if case["history"] else None
        if (latest["id"] if latest else None) != expected:
            raise ValueError("The complaint changed. Reload the dashboard before correcting it.")
        before = {field: current[field] for field in clean if field in current.keys()}
        effective = {field: value for field, value in clean.items() if current[field] != value}
        missing = [field for field in (current["missing_fields"] or "").split(",") if field]
        if "issue" in clean:
            missing = [field for field in missing if field != "issue"]
            if clean["issue"] is None:
                missing.append("issue")
        if "location" in clean:
            missing = [field for field in missing if field != "location"]
            if clean["location"] == "unknown":
                missing.append("location")
        corrected_missing = ",".join(dict.fromkeys(missing))
        if corrected_missing != (current["missing_fields"] or ""):
            effective["missing_fields"] = corrected_missing
            before["missing_fields"] = current["missing_fields"]
        if not effective:
            raise ValueError("No extracted fields changed; there is nothing to record.")
        after = {field: value for field, value in effective.items()}
        assignments = ",".join(f"{field}=?" for field in effective)
        conn.execute(f"UPDATE complaints SET {assignments} WHERE id=?",
                     (*effective.values(), complaint_id))
        details = {"before": {field: before[field] for field in after}, "after": after}
        event = conn.execute(
            "INSERT INTO ledger(complaint_id,event_type,actor,reason,reason_code,details) "
            "VALUES(?,'extraction_corrected',?,?,?,?)",
            (complaint_id, actor["email"], reason, reason_code,
             json.dumps(details, ensure_ascii=True, sort_keys=True)))
        conn.commit()
        return {"event_id": event.lastrowid, "changes": after}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
