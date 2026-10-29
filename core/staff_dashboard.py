"""Department- and geography-scoped staff dashboard services.

SLA values are prototype assumptions. All operational decisions append ledger rows;
the complaint's original text and prior decisions are never overwritten.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from .auth import STAFF_ROLES, get_active_user
from .config import REASON_CODES
from .db import fetch_all, get_conn
from .geo import is_demo_citywide_user, is_geography_admin, scoped_complaints
from .intake import normalize_location
from .ownership import check_suggestions, detect_bounce, repeat_reassignment
from .safety import assess_safety
from .suggestions import selected_department

DEPARTMENTS = {
    "Roads Dept", "Drainage Dept", "Sanitation Dept", "Electricity Dept",
    "Water Supply Dept", "Grievance Cell",
}
STAFF_ACTION_ROLES = {"government", "operator", "reviewer"}
ACTION_EVENTS = {"dispatch", "clarify", "resolve", "transfer"}
STATUS_EVENTS = ACTION_EVENTS | {"accept", "edit", "escalate", "challenge_resolved"}
_DEMO_SLA_DEFAULT_DAYS = 7


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _actor(user):
    if not user or not user.get("id") or user.get("role") not in STAFF_ROLES:
        raise PermissionError("A signed-in government staff account is required.")
    fresh = get_active_user(user["id"])
    if (not fresh or fresh.get("role") != user.get("role")
            or fresh.get("email", "").casefold() != (user.get("email") or "").casefold()):
        raise PermissionError("The staff account is no longer active or does not match this session.")
    return fresh


def _sla_days():
    raw = os.environ.get("NAGRIKSETU_DEMO_SLA_DAYS", str(_DEMO_SLA_DEFAULT_DAYS))
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = _DEMO_SLA_DEFAULT_DAYS
    return max(1, min(days, 90))


def _sla(created_at, is_resolved, now=None):
    now = now or _now_utc()
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    created = _parse_time(created_at)
    if not created:
        return {"deadline": None, "label": "Deadline unavailable", "overdue": False,
                "assumption": "DEMO SLA assumption"}
    deadline = created + timedelta(days=_sla_days())
    remaining = deadline - now
    if is_resolved:
        label, overdue = "Resolved", False
    elif remaining.total_seconds() < 0:
        label, overdue = "Overdue", True
    elif remaining.days == 0:
        label, overdue = "Due today", False
    else:
        label, overdue = f"{remaining.days} days left", False
    return {"deadline": deadline.isoformat(timespec="seconds"), "label": label,
            "overdue": overdue, "assumption": f"DEMO SLA · {_sla_days()} days from submission"}


def _load_scoped_rows(actor):
    # Geography scope is evaluated from the active persisted identity. Only a
    # configured geography administrator may include unassigned legacy cases.
    rows = scoped_complaints(actor, include_unassigned=is_geography_admin(actor.get("email", "")))
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    marks = ",".join("?" for _ in ids)
    events = fetch_all(
        f"SELECT id,complaint_id,event_type,actor,suggested_dept,final_dept,reason,reason_code,details,created_at "
        f"FROM ledger WHERE complaint_id IN ({marks}) ORDER BY id", ids)
    suggestions = fetch_all(
        f"SELECT s.* FROM suggestions s JOIN (SELECT complaint_id,MAX(id) id FROM suggestions "
        f"WHERE complaint_id IN ({marks}) GROUP BY complaint_id) latest ON latest.id=s.id", ids)
    challenges = fetch_all(
        f"SELECT * FROM challenges WHERE complaint_id IN ({marks}) AND status='open' ORDER BY id", ids)
    split_rows = fetch_all(f"SELECT parent_id,status FROM split_proposals WHERE parent_id IN ({marks})", ids)
    event_map, suggestion_map, challenge_map = {}, {}, {}
    split_map = {row["parent_id"]: row["status"] for row in split_rows}
    for event in events:
        event_map.setdefault(event["complaint_id"], []).append(event)
    for suggestion in suggestions:
        suggestion_map[suggestion["complaint_id"]] = suggestion
    for challenge in challenges:
        challenge_map.setdefault(challenge["complaint_id"], []).append(challenge)

    result = []
    for row in rows:
        history = event_map.get(row["id"], [])
        suggestion = suggestion_map.get(row["id"])
        top3 = json.loads(suggestion["top3"] or "[]") if suggestion else []
        selected = selected_department({"top3": top3,
                                        "abstained": bool(suggestion and suggestion["abstained"])})
        # Latest human final department wins. Otherwise the model output remains a
        # recommendation and is used only to populate the officer's assignment inbox.
        final_owner = next((event["final_dept"] for event in reversed(history)
                            if event["final_dept"]), None)
        current_owner = final_owner or selected
        open_challenges = challenge_map.get(row["id"], [])
        decision_events = [event for event in history if event["event_type"] in STATUS_EVENTS]
        last_action = decision_events[-1] if decision_events else None
        if (last_action and last_action["event_type"] == "clarify"
                and any(item["raised_by"] == "staff" for item in open_challenges)):
            status = "awaiting clarification"
        elif open_challenges:
            status = "pending"
        elif last_action and last_action["event_type"] == "resolve":
            status = "resolved"
        elif not last_action:
            status = "new"
        else:
            status = "pending"
        safety = assess_safety(row.get("original_text") or "")
        overlaps = check_suggestions([item.get("dept", "") for item in top3], row.get("original_text") or "")
        enriched = dict(row)
        enriched.update({
            "suggestion": suggestion,
            "top3": top3,
            "suggested_department": selected,
            "current_department": current_owner,
            # Missing routing output is manual review too; it must never appear as
            # a confidently routed case just because a suggestion row is absent.
            "uncertain": suggestion is None or bool(suggestion["abstained"]),
            "open_challenges": open_challenges,
            "split_status": split_map.get(row["id"]),
            "status": status,
            "last_action": last_action,
            "history": history,
            "safety": safety,
            "ownership_conflicts": overlaps,
            "sla": _sla(row.get("created_at"), status == "resolved"),
        })
        # Operators see their department's routed work plus uncertain cases in
        # the manual queue. A reviewer/auditor sees only authorized geography.
        if actor["role"] == "operator":
            if not actor.get("department"):
                raise PermissionError("This officer account has no assigned department.")
            if (current_owner != actor["department"] and not enriched["uncertain"]
                    and not is_demo_citywide_user(actor)):
                continue
        result.append(enriched)
    return result


def list_staff_cases(user, filters=None):
    actor = _actor(user)
    rows = _load_scoped_rows(actor)
    filters = filters or {}
    query = normalize_location(str(filters.get("search") or "")) or ""
    for row in rows:
        row["_search"] = normalize_location(" ".join(str(row.get(key) or "") for key in
                                  ("case_id", "original_text", "issue", "location", "location_normalized",
                                   "area_name", "state_name", "city_name", "current_department"))) or ""
    if query:
        rows = [row for row in rows if query in row["_search"]]
    for key in ("status", "area_name", "issue", "language"):
        value = filters.get(key)
        if value:
            rows = [row for row in rows if str(row.get(key) or "") == str(value)]
    if filters.get("safety"):
        rows = [row for row in rows if row["safety"]["flagged"]]
    if filters.get("ownership_conflict"):
        rows = [row for row in rows if row["ownership_conflicts"]]
    if filters.get("overdue"):
        rows = [row for row in rows if row["sla"]["overdue"]]
    after = _parse_time(filters.get("submitted_after")) if filters.get("submitted_after") else None
    before = _parse_time(filters.get("submitted_before")) if filters.get("submitted_before") else None
    if after:
        rows = [row for row in rows if (_parse_time(row.get("created_at")) or after) >= after]
    if before:
        rows = [row for row in rows if (_parse_time(row.get("created_at")) or before) <= before]
    rows.sort(key=lambda row: (_parse_time(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                               row["id"]), reverse=True)
    return rows


def get_dashboard_summary(user):
    cases = list_staff_cases(user)
    return {
        "new": sum(case["status"] == "new" for case in cases),
        "pending": sum(case["status"] == "pending" for case in cases),
        "overdue": sum(case["sla"]["overdue"] for case in cases),
        "awaiting_clarification": sum(case["status"] == "awaiting clarification" for case in cases),
        "resolved": sum(case["status"] == "resolved" for case in cases),
        "total": len(cases),
    }


def get_ward_hotspots(user, *, window_days=7, now=None):
    """Return scoped, explicitly synthetic/demo complaint concentration summaries."""
    if window_days < 1 or window_days > 90:
        raise ValueError("Choose a hotspot window between 1 and 90 days.")
    actor = _actor(user)
    cases = list_staff_cases(actor)
    now = now or _now_utc()
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    start = now - timedelta(days=window_days)
    baseline_start = start - timedelta(days=window_days)
    grouped = {}
    for case in cases:
        created = _parse_time(case.get("created_at"))
        if not created or created < baseline_start or created > now:
            continue
        location = case.get("area_name") or case.get("location") or "Area not recorded"
        issue = case.get("issue") or "Uncategorized"
        item = grouped.setdefault((location, issue), {"observed": 0, "baseline": 0,
                                                       "pending": 0, "pending_baseline": 0,
                                                       "synthetic": False})
        if created >= start:
            item["observed"] += 1
            item["pending"] += int(case["status"] != "resolved")
        else:
            item["baseline"] += 1
            item["pending_baseline"] += int(case["status"] != "resolved")
        item["synthetic"] = item["synthetic"] or bool(case.get("is_synthetic"))
    results = []
    for (location, issue), values in grouped.items():
        results.append({"location": location, "issue": issue, "count": values["pending"],
                        "observed_count": values["observed"], "baseline_count": values["baseline"],
                        "pending_baseline": values["pending_baseline"],
                        "window_days": window_days,
                        "window_start": start.isoformat(timespec="seconds"),
                        "window_end": now.isoformat(timespec="seconds"),
                        "sample_status": "LOW SAMPLE · exploratory" if values["observed"] < 3 else "",
                        "data_label": "SIMULATED / SYNTHETIC" if values["synthetic"] else "OBSERVED"})
    return sorted(results, key=lambda item: (-item["count"], -item["observed_count"], item["location"]))[:5]


def _authorized_case(user, complaint_id, *, writing=False):
    actor = _actor(user)
    if writing and actor["role"] not in STAFF_ACTION_ROLES:
        raise PermissionError("Auditor accounts have read-only access.")
    cases = list_staff_cases(actor)
    case = next((item for item in cases if item["id"] == int(complaint_id)), None)
    if not case:
        raise PermissionError("This complaint is outside your department or selected service areas.")
    if writing and case["status"] == "resolved":
        raise ValueError("This complaint is resolved and cannot receive another action.")
    return actor, case


def perform_staff_action(user, complaint_id, action, *, note, reason_code=None, target_department=None):
    """Append a permitted officer decision after rechecking account and case scope."""
    actor, case = _authorized_case(user, complaint_id, writing=True)
    action = (action or "").strip().casefold()
    note = (note or "").strip()
    if action not in ACTION_EVENTS:
        raise ValueError("Choose a supported staff action.")
    if not note:
        raise ValueError("A short action note is required.")
    if len(note) > 1000 or any(ord(char) < 32 and char not in "\t\r\n" for char in note):
        raise ValueError("The action note must be at most 1000 characters and contain no control characters.")
    if action == "transfer":
        if reason_code not in REASON_CODES:
            raise ValueError("Choose a transfer reason code.")
        target_department = (target_department or "").strip()
        if target_department not in DEPARTMENTS:
            raise ValueError("Choose a valid receiving department.")
        if target_department == case["current_department"]:
            raise ValueError("Choose a department different from the current owner.")
    if action == "resolve" and reason_code and reason_code not in REASON_CODES:
        raise ValueError("Choose a valid reason code or leave it blank.")

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        persisted = conn.execute("SELECT active,role,department FROM users WHERE id=?", (actor["id"],)).fetchone()
        if (not persisted or not persisted["active"] or persisted["role"] != actor["role"]
                or persisted["department"] != actor.get("department")):
            raise PermissionError("The staff account changed. Sign in again before acting.")
        # Repeat the complete area and department check inside the write transaction.
        _, current_case = _authorized_case(actor, complaint_id, writing=True)
        old_event_id = case["history"][-1]["id"] if case["history"] else None
        new_event_id = current_case["history"][-1]["id"] if current_case["history"] else None
        if (current_case["current_department"] != case["current_department"]
                or current_case["status"] != case["status"] or old_event_id != new_event_id):
            raise ValueError("The complaint changed. Reload the dashboard before acting.")
        if action == "clarify":
            if conn.execute("SELECT 1 FROM challenges WHERE complaint_id=? AND status='open' LIMIT 1",
                            (complaint_id,)).fetchone():
                raise ValueError("A review or clarification request is already open for this complaint.")
            challenge = conn.execute(
                "INSERT INTO challenges (complaint_id,raised_by,reason) VALUES (?,'staff',?)",
                (complaint_id, note))
            details = {"status": "awaiting clarification", "challenge_id": challenge.lastrowid}
        elif action == "dispatch":
            details = {"status": "pending", "integration": "not connected; action recorded only"}
        elif action == "resolve":
            details = {"status": "resolved", "resolution_note": note}
        else:
            details = {"status": "pending", "from_department": case["current_department"],
                       "to_department": target_department}
        reason = note
        code = reason_code if action in {"transfer", "resolve"} else None
        final_dept = target_department if action == "transfer" else case["current_department"]
        details.update({"prior_status": case["status"], "new_status": details["status"],
                        "previous_department": case["current_department"],
                        "new_department": final_dept, "reason_code": code, "note": note})
        conn.execute(
            "INSERT INTO ledger (complaint_id,event_type,actor,suggested_dept,final_dept,reason,reason_code,details) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (complaint_id, action, actor["email"], case["suggested_department"], final_dept,
             reason, code, json.dumps(details, ensure_ascii=False)))
        conn.commit()
        return {"event_id": conn.execute("SELECT last_insert_rowid()").fetchone()[0], **details}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_staff_case_detail(user, complaint_id):
    actor, case = _authorized_case(user, complaint_id)
    complaint = fetch_all("SELECT * FROM complaints WHERE id=?", (complaint_id,))[0]
    suggestion_rows = fetch_all("SELECT * FROM suggestions WHERE complaint_id=? ORDER BY id", (complaint_id,))
    evidence = []
    for suggestion in suggestion_rows:
        evidence.append({"method": suggestion["method"], "created_at": suggestion["created_at"],
                         "abstained": bool(suggestion["abstained"]),
                         "top3": json.loads(suggestion["top3"] or "[]"),
                         "evidence": json.loads(suggestion["evidence"] or "{}")})
    suggestion_items = case["top3"]
    conflicts = check_suggestions([item.get("dept", "") for item in suggestion_items],
                                  complaint["original_text"])
    from .duplicates import find_candidates
    duplicates = find_candidates(complaint_id, actor_user=actor)
    subissues = fetch_all("SELECT id,case_id,original_text,issue,location,created_at FROM complaints "
                          "WHERE parent_id=? AND is_active=1 ORDER BY id", (complaint_id,))
    events = [{"event_type": "submitted", "actor": "public user", "created_at": complaint["created_at"],
               "reason": "Complaint received", "reason_code": None, "details": None}]
    for item in evidence:
        events.append({"event_type": "routing_suggestion", "actor": "system suggestion",
                       "created_at": item["created_at"], "reason": "Recommendation for human review",
                       "reason_code": None, "details": json.dumps(item, ensure_ascii=False)})
    events.extend(case["history"])
    events.sort(key=lambda item: (_parse_time(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                                  item.get("id", 0)))
    from .ownership import detect_bounce, repeat_reassignment
    bounce = detect_bounce(case["history"])
    repeated = repeat_reassignment(case["history"])
    attachments = []
    from .auth import verify_permission
    if verify_permission(actor["role"], "can_view_attachments"):
        from .attachment import get_attachments
        attachments = get_attachments(complaint_id, actor["role"], actor_user=actor)
    return {"complaint": complaint, "status": case["status"], "sla": case["sla"],
            "suggested_department": case["suggested_department"],
            "current_department": case["current_department"], "uncertain": case["uncertain"],
            "split_status": case.get("split_status"),
            "routing_history": evidence, "ownership_conflicts": conflicts,
            "safety": assess_safety(complaint["original_text"]), "duplicates": duplicates,
            "confirmed_subissues": subissues,
            "open_challenges": case["open_challenges"], "timeline": events,
            "bounce_warning": bounce, "repeat_reassignment": repeated,
            "attachments": attachments, "actor": actor}
