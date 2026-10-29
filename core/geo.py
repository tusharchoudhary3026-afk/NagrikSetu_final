"""Structured geography, append-only staff scope requests, and server-side area checks."""
import csv
import json
import os
from pathlib import Path

from .config import ROOT
from .db import fetch_all, get_conn

STAFF_ROLES = {"government", "operator", "reviewer", "auditor"}
CITY_WIDE_AREA_CODE = "*"
DEMO_STAFF_ASSIGNMENTS = {
    "operator01@staff.nagriksetu-demo.example": "operator",
    "reviewer01@staff.nagriksetu-demo.example": "reviewer",
    "auditor01@staff.nagriksetu-demo.example": "auditor",
}
DEMO_ASSIGNMENT_ACTOR = "system:demo-admin"
DEMO_ASSIGNMENT_REASON = "DEMO MODE: pre-approved synthetic assignment for all Gwalior areas."

COVERAGE_PATH = ROOT / "data" / "geo_coverage.json"
GOV_ALLOWLIST_PATH = ROOT / "data" / "gov_allowlist.csv"
GEO_ADMIN_ALLOWLIST_PATH = ROOT / "data" / "geo_admin_allowlist.csv"


def area_options() -> dict:
    """Return state -> city -> area choices, keyed by stable codes."""
    data = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    states = {}
    for state in data.get("states", []):
        cities = {}
        for city in state.get("cities", []):
            cities[city["code"]] = {
                "name": city["name"],
                "areas": {area["code"]: area["name"] for area in city.get("areas", [])},
            }
        states[state["code"]] = {"name": state["name"], "cities": cities}
    return states


def demo_staff_seed_enabled() -> bool:
    """Require both demo mode and an explicit opt-in for the demo-only seed."""
    from .auth import demo_mode
    enabled = os.environ.get("NYAYAFLOW_SEED_DEMO_STAFF", "0").strip().casefold()
    return demo_mode() and enabled in {"1", "true", "yes", "on"}


def seed_demo_staff_assignments() -> int:
    """Append city-wide approvals for only the three reserved synthetic accounts."""
    if not demo_staff_seed_enabled():
        return 0
    users = fetch_all(
        "SELECT u.id,u.email,u.role,u.active,d.registered,d.status,d.role AS directory_role "
        "FROM users u JOIN staff_directory d ON d.staff_email=u.email "
        "WHERE lower(u.email) IN (?,?,?)",
        tuple(DEMO_STAFF_ASSIGNMENTS),
    )
    seeded = 0
    for user in users:
        email = user["email"].strip().casefold()
        expected_role = DEMO_STAFF_ASSIGNMENTS.get(email)
        if (not expected_role or user["role"] != expected_role or user["directory_role"] != expected_role
                or not user["active"] or not user["registered"] or user["status"] != "active"):
            continue
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            latest = conn.execute(
                "SELECT id FROM government_area_events WHERE user_id=? AND state_code=? AND city_code=? "
                "AND area_code=? ORDER BY id DESC LIMIT 1",
                (user["id"], "mp", "mp-gwalior", CITY_WIDE_AREA_CODE),
            ).fetchone()
            # Any prior event, including a revocation, is authoritative. The
            # seed is one-time and never silently restores access after an admin acts.
            if latest:
                conn.commit()
                continue
            conn.execute(
                "INSERT INTO government_area_events "
                "(user_id,state_code,city_code,area_code,event_type,actor_email,reason) "
                "VALUES(?,?,?,?,'approved',?,?)",
                (user["id"], "mp", "mp-gwalior", CITY_WIDE_AREA_CODE,
                 DEMO_ASSIGNMENT_ACTOR, DEMO_ASSIGNMENT_REASON),
            )
            conn.commit()
            seeded += 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return seeded


def known_area_for_location(state_code: str | None, city_code: str | None,
                            location: str | None) -> dict | None:
    """Resolve an exact known area name/alias without changing the entered location."""
    if not state_code or not city_code or not location:
        return None
    from .intake import normalize_location
    aliases = {
        "mp-gwalior-gole-ka-mandir": {"gole ka mandir", "गोले का मंदिर"},
    }
    options = area_options()
    try:
        city = options[state_code]["cities"][city_code]
    except KeyError:
        return None
    entered = normalize_location(location)
    for area_code, area_name in city["areas"].items():
        candidates = {normalize_location(area_name),
                      *(normalize_location(alias) for alias in aliases.get(area_code, set()))}
        if entered in candidates:
            return {"state_code": state_code, "city_code": city_code,
                    "area_code": area_code, "area_name": area_name}
    return None


def geography_names(state_code: str, city_code: str, area_code: str) -> dict:
    options = area_options()
    try:
        state = options[state_code]
        city = state["cities"][city_code]
        area_name = city["areas"][area_code]
    except (KeyError, TypeError) as exc:
        raise ValueError("Choose a valid state, city, and area combination.") from exc
    return {"state_code": state_code, "state_name": state["name"],
            "city_code": city_code, "city_name": city["name"],
            "area_code": area_code, "area_name": area_name}


def partial_geography_names(state_code: str | None, city_code: str | None = None,
                            area_code: str | None = None) -> dict:
    """Validate known geographic levels while preserving unknown lower levels."""
    if not state_code:
        return {key: None for key in ("state_code", "state_name", "city_code", "city_name",
                                      "area_code", "area_name")}
    options = area_options()
    if state_code not in options:
        raise ValueError("Choose a valid state.")
    state = options[state_code]
    result = {"state_code": state_code, "state_name": state["name"],
              "city_code": None, "city_name": None, "area_code": None, "area_name": None}
    if not city_code:
        return result
    if city_code not in state["cities"]:
        raise ValueError("Choose a city within the selected state.")
    city = state["cities"][city_code]
    result.update({"city_code": city_code, "city_name": city["name"]})
    if not area_code:
        return result
    if area_code not in city["areas"]:
        raise ValueError("Choose an area within the selected city.")
    result.update({"area_code": area_code, "area_name": city["areas"][area_code]})
    return result


def _gov_allowlist_rows():
    try:
        with GOV_ALLOWLIST_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except FileNotFoundError:
        return []


def _latest_event(user_id, state_code, city_code, area_code):
    rows = fetch_all(
        "SELECT * FROM government_area_events WHERE user_id=? AND state_code=? AND city_code=? "
        "AND area_code=? ORDER BY id DESC LIMIT 1",
        (user_id, state_code, city_code, area_code))
    return rows[0] if rows else None


def _verified_staff(user_id: int, email: str | None = None) -> dict:
    from .auth import get_active_user
    identity = get_active_user(int(user_id))
    if (not identity or identity.get("role") not in STAFF_ROLES
            or (email and identity.get("email", "").casefold() != email.strip().casefold())):
        raise PermissionError("An active, registered staff-directory account is required.")
    return identity


def _append_event(user_id, state_code, city_code, area_code, event_type, actor_email, reason):
    if area_code == CITY_WIDE_AREA_CODE:
        partial_geography_names(state_code, city_code, None)
    else:
        geography_names(state_code, city_code, area_code)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO government_area_events (user_id,state_code,city_code,area_code,event_type,actor_email,reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (int(user_id), state_code, city_code, area_code, event_type,
             (actor_email or "").strip().casefold(), (reason or "").strip()))
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def configure_staff_location(user: dict, state_code: str, city_code: str, area_code: str,
                             reason: str = "Staff requested an area assignment."):
    """Sync exact allowlist grants; otherwise create an auditable admin-review request."""
    if not user or user.get("role") not in STAFF_ROLES:
        raise PermissionError("Only an authenticated staff account can configure a staff area.")
    _verified_staff(user.get("id"), user.get("email"))
    names = geography_names(state_code, city_code, area_code)
    sync_allowlisted_assignments(user["id"], user["email"])
    existing = staff_area_scope(user["id"])
    if any((item["state_code"], item["city_code"], item["area_code"]) ==
           (state_code, city_code, area_code) for item in existing):
        return {"status": "approved", **names}
    latest = _latest_event(user["id"], state_code, city_code, area_code)
    if latest and latest["event_type"] == "requested":
        return {"status": "pending", **names}
    request_id = request_area_assignment(user["id"], state_code, city_code, area_code,
                                         user["email"], reason=reason)
    return {"status": "pending", "request_id": request_id, **names}


def staff_location_snapshot(user: dict) -> dict:
    if not user or user.get("role") not in STAFF_ROLES:
        raise PermissionError("Government staff access is required.")
    _verified_staff(user.get("id"), user.get("email"))
    sync_allowlisted_assignments(user["id"], user["email"])
    scope = staff_area_scope(user["id"])
    pending = fetch_all("SELECT * FROM government_area_events WHERE user_id=? AND event_type='requested' "
                        "AND id=(SELECT MAX(last.id) FROM government_area_events last WHERE last.user_id=? "
                        "AND last.state_code=government_area_events.state_code "
                        "AND last.city_code=government_area_events.city_code "
                        "AND last.area_code=government_area_events.area_code) ORDER BY id DESC LIMIT 1",
                        (user["id"], user["id"]))
    rejected = fetch_all("SELECT * FROM government_area_events WHERE user_id=? AND event_type='rejected' "
                         "ORDER BY id DESC LIMIT 1", (user["id"],))
    return {"assigned": scope, "pending": pending[0] if pending else None,
            "last_rejection": rejected[0] if rejected else None}


def sync_allowlisted_assignments(user_id: int, email: str) -> int:
    """Append approved grants for the exact geographic scopes in the staff allowlist."""
    email = (email or "").strip().casefold()
    _verified_staff(user_id, email)
    grants = []
    for row in _gov_allowlist_rows():
        if (row.get("email") or "").strip().casefold() == email:
            for area_code in (row.get("area_codes") or "").split(";"):
                area_code = area_code.strip()
                if area_code:
                    grants.append((row.get("state_code", "").strip(),
                                   row.get("city_code", "").strip(), area_code))
    written = 0
    for state_code, city_code, area_code in grants:
        geography_names(state_code, city_code, area_code)
        latest = _latest_event(user_id, state_code, city_code, area_code)
        # A configured grant seeds the scope once. Later administrator revocation
        # remains effective and is never silently undone at the next login.
        if not latest:
            _append_event(user_id, state_code, city_code, area_code, "approved",
                          "system:gov_allowlist", "Area pre-approved in the protected government allowlist.")
            written += 1
    return written


def staff_area_scope(user_id: int) -> list[dict]:
    """Return only areas whose most recent assignment event is an approval."""
    rows = fetch_all(
        "SELECT e.state_code,e.city_code,e.area_code,e.actor_email,e.reason,e.created_at,u.email,u.role "
        "FROM government_area_events e JOIN users u ON u.id=e.user_id "
        "WHERE e.user_id=? AND e.event_type='approved' "
        "AND e.id=(SELECT MAX(last.id) FROM government_area_events last WHERE last.user_id=e.user_id "
        "AND last.state_code=e.state_code AND last.city_code=e.city_code AND last.area_code=e.area_code) "
        "ORDER BY e.state_code,e.city_code,e.area_code", (int(user_id),))
    if any(row["actor_email"] == DEMO_ASSIGNMENT_ACTOR for row in rows):
        enabled = demo_staff_seed_enabled()
        rows = [row for row in rows if row["actor_email"] != DEMO_ASSIGNMENT_ACTOR or (
            enabled and DEMO_STAFF_ASSIGNMENTS.get(row["email"].strip().casefold()) == row["role"])]
    for row in rows:
        if row["area_code"] == CITY_WIDE_AREA_CODE:
            names = {"state_name": area_options()[row["state_code"]]["name"],
                     "city_name": area_options()[row["state_code"]]["cities"][row["city_code"]]["name"],
                     "area_name": "All areas (city-wide)"}
        else:
            names = geography_names(row["state_code"], row["city_code"], row["area_code"])
        row.update({key: names[key] for key in ("state_name", "city_name", "area_name")})
    return rows


def is_demo_citywide_user(user: dict, scope: list[dict] | None = None) -> bool:
    """Confirm the exact opt-in demo account has an active city-wide demo grant."""
    if not user or not demo_staff_seed_enabled():
        return False
    email = (user.get("email") or "").strip().casefold()
    if DEMO_STAFF_ASSIGNMENTS.get(email) != user.get("role"):
        return False
    scope = scope if scope is not None else staff_area_scope(user.get("id"))
    return any(row["actor_email"] == DEMO_ASSIGNMENT_ACTOR
               and row["area_code"] == CITY_WIDE_AREA_CODE
               and row["state_code"] == "mp" and row["city_code"] == "mp-gwalior"
               for row in scope)


def request_area_assignment(user_id: int, state_code: str, city_code: str, area_code: str,
                            actor_email: str, reason: str = "Staff requested an area assignment.") -> int:
    names = geography_names(state_code, city_code, area_code)
    email = (actor_email or "").strip().casefold()
    _verified_staff(user_id, email)
    users = fetch_all("SELECT email,role,active FROM users WHERE id=?", (int(user_id),))
    if not users or not users[0]["active"] or users[0]["role"] not in STAFF_ROLES:
        raise PermissionError("Only an active staff account can request an assigned area.")
    if users[0]["email"].casefold() != email:
        raise PermissionError("The signed-in staff account does not match this area request.")
    latest = _latest_event(user_id, state_code, city_code, area_code)
    if latest and latest["event_type"] in {"requested", "approved"}:
        raise ValueError("This area is already pending or assigned to your account.")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter why this service-area assignment is needed.")
    return _append_event(user_id, names["state_code"], names["city_code"], names["area_code"],
                         "requested", email, reason)


def _geo_admin_emails() -> set[str]:
    emails = {item.strip().casefold() for item in os.environ.get("NYAYAFLOW_GEO_ADMIN_EMAILS", "").split(",")
              if item.strip()}
    try:
        with GEO_ADMIN_ALLOWLIST_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
            emails.update((row.get("email") or "").strip().casefold()
                          for row in csv.DictReader(stream) if (row.get("email") or "").strip())
    except FileNotFoundError:
        pass
    return emails


def is_geography_admin(email: str) -> bool:
    return (email or "").strip().casefold() in _geo_admin_emails()


def _require_geo_admin(actor_email: str):
    actor_email = (actor_email or "").strip().casefold()
    if not is_geography_admin(actor_email):
        raise PermissionError("Only a configured geography administrator may approve area access.")
    rows = fetch_all("SELECT id,role,active FROM users WHERE email=?", (actor_email,))
    if not rows or not rows[0]["active"] or rows[0]["role"] not in {"government", "reviewer"}:
        raise PermissionError("A geography administrator must sign in with an active reviewer account.")
    from .auth import authorize_user_action
    authorize_user_action(rows[0]["id"], "can_review")


def approve_area_request(event_id: int, actor_email: str, reason: str):
    _require_geo_admin(actor_email)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter the verification reason for this area approval.")
    rows = fetch_all("SELECT * FROM government_area_events WHERE id=?", (int(event_id),))
    if not rows or rows[0]["event_type"] != "requested":
        raise ValueError("This area request is not pending approval.")
    request = rows[0]
    latest = _latest_event(request["user_id"], request["state_code"], request["city_code"], request["area_code"])
    if not latest or latest["id"] != request["id"]:
        raise ValueError("This area request has already been reviewed.")
    staff = fetch_all("SELECT role,active FROM users WHERE id=?", (request["user_id"],))
    if not staff or not staff[0]["active"] or staff[0]["role"] not in STAFF_ROLES:
        raise ValueError("Only an active staff account can receive area access.")
    _verified_staff(request["user_id"])
    if request["actor_email"].casefold() == actor_email.strip().casefold():
        raise PermissionError("Staff cannot approve their own area request.")
    return _append_event(request["user_id"], request["state_code"], request["city_code"], request["area_code"],
                         "approved", actor_email, reason)


def reject_area_request(event_id: int, actor_email: str, reason: str):
    _require_geo_admin(actor_email)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter a reason for rejecting this area request.")
    rows = fetch_all("SELECT * FROM government_area_events WHERE id=?", (int(event_id),))
    if not rows or rows[0]["event_type"] != "requested":
        raise ValueError("This area request is not pending review.")
    request = rows[0]
    latest = _latest_event(request["user_id"], request["state_code"], request["city_code"], request["area_code"])
    if not latest or latest["id"] != request["id"]:
        raise ValueError("This area request has already been reviewed.")
    return _append_event(request["user_id"], request["state_code"], request["city_code"], request["area_code"],
                         "rejected", actor_email, reason)


def active_area_assignments(actor_email: str) -> list[dict]:
    _require_geo_admin(actor_email)
    rows = fetch_all(
        "SELECT e.id,e.user_id,e.state_code,e.city_code,e.area_code,e.actor_email,e.reason,e.created_at,"
        "u.name,u.email FROM government_area_events e JOIN users u ON u.id=e.user_id "
        "WHERE e.event_type='approved' AND e.id=(SELECT MAX(last.id) FROM government_area_events last "
        "WHERE last.user_id=e.user_id AND last.state_code=e.state_code AND last.city_code=e.city_code "
        "AND last.area_code=e.area_code) ORDER BY e.id DESC")
    return rows


def revoke_area_assignment(event_id: int, actor_email: str, reason: str):
    _require_geo_admin(actor_email)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter a reason for revoking this area assignment.")
    rows = fetch_all("SELECT * FROM government_area_events WHERE id=? AND event_type='approved'", (int(event_id),))
    if not rows:
        raise ValueError("This area assignment is not active.")
    grant = rows[0]
    latest = _latest_event(grant["user_id"], grant["state_code"], grant["city_code"], grant["area_code"])
    if not latest or latest["id"] != grant["id"]:
        raise ValueError("This area assignment has already changed.")
    return _append_event(grant["user_id"], grant["state_code"], grant["city_code"], grant["area_code"],
                         "revoked", actor_email, reason)


def pending_area_requests(actor_email: str) -> list[dict]:
    _require_geo_admin(actor_email)
    return fetch_all(
        "SELECT e.id,e.user_id,e.state_code,e.city_code,e.area_code,e.actor_email AS requested_by,e.reason,e.created_at,"
        "u.name,u.email FROM government_area_events e JOIN users u ON u.id=e.user_id "
        "WHERE e.event_type='requested' AND e.id=(SELECT MAX(last.id) FROM government_area_events last "
        "WHERE last.user_id=e.user_id AND last.state_code=e.state_code AND last.city_code=e.city_code "
        "AND last.area_code=e.area_code) ORDER BY e.id")


def require_complaint_area_access(user: dict, complaint_id: int):
    """Authorize a complaint by persisted actor identity and its structured area."""
    if not user or user.get("role") not in STAFF_ROLES or not user.get("id"):
        raise PermissionError("An authenticated government staff account is required.")
    active = fetch_all("SELECT email,role,active,department FROM users WHERE id=?", (user.get("id"),))
    if (not active or not active[0]["active"] or active[0]["role"] not in STAFF_ROLES
            or active[0]["role"] != user.get("role")
            or active[0]["email"].casefold() != (user.get("email") or "").casefold()):
        raise PermissionError("The government session is no longer valid.")
    from .auth import get_active_user
    identity = get_active_user(user["id"])
    if not identity or identity["email"].casefold() != (user.get("email") or "").casefold():
        raise PermissionError("The staff directory record is not active.")
    rows = fetch_all("SELECT state_code,city_code,area_code FROM complaints WHERE id=?", (int(complaint_id),))
    if not rows:
        raise ValueError("Complaint not found.")
    case = rows[0]
    scope = staff_area_scope(user["id"])
    if not case["state_code"] or not case["city_code"]:
        if not is_geography_admin(user.get("email", "")):
            raise PermissionError("This complaint has no verified area and is restricted to geography administrators.")
    else:
        city_wide = any(r["state_code"] == case["state_code"] and r["city_code"] == case["city_code"]
                        and r["area_code"] == CITY_WIDE_AREA_CODE for r in scope)
        exact_area = any((r["state_code"], r["city_code"], r["area_code"])
                         == (case["state_code"], case["city_code"], case["area_code"])
                         for r in scope)
        if not city_wide and (not case["area_code"] or not exact_area):
            raise PermissionError("This complaint is outside your assigned area.")
    if user.get("role") == "operator" and not is_demo_citywide_user(user, scope):
        account = fetch_all("SELECT department FROM users WHERE id=? AND active=1", (user["id"],))
        if not account or not account[0]["department"]:
            raise PermissionError("This officer account has no assigned department.")
        final = fetch_all("SELECT final_dept FROM ledger WHERE complaint_id=? AND final_dept IS NOT NULL "
                          "ORDER BY id DESC LIMIT 1", (int(complaint_id),))
        owner = final[0]["final_dept"] if final else None
        if not owner:
            suggestions = fetch_all("SELECT top3,abstained FROM suggestions WHERE complaint_id=? ORDER BY id DESC LIMIT 1",
                                    (int(complaint_id),))
            if suggestions:
                import json
                from .suggestions import selected_department
                owner = selected_department({"top3": json.loads(suggestions[0]["top3"] or "[]"),
                                             "abstained": bool(suggestions[0]["abstained"])})
        if owner != account[0]["department"]:
            raise PermissionError("This complaint is outside your assigned department.")


def authorize_staff_complaint(actor_role: str, complaint_id: int, actor_user: dict | None = None):
    """Enforce area scope for web-backed government service mutations and reads."""
    if actor_role in STAFF_ROLES:
        if (not actor_user or actor_user.get("role") not in STAFF_ROLES
                or actor_user.get("role") != actor_role):
            raise PermissionError("Pass the authenticated staff account to authorize its assigned area.")
        active = fetch_all("SELECT email,role,active FROM users WHERE id=?", (actor_user.get("id"),))
        if (not active or not active[0]["active"] or active[0]["role"] not in STAFF_ROLES
                or active[0]["role"] != actor_user.get("role")
                or active[0]["email"].casefold() != (actor_user.get("email") or "").casefold()):
            raise PermissionError("The government session is no longer valid.")
        from .auth import get_active_user
        identity = get_active_user(actor_user["id"])
        if not identity or identity["email"].casefold() != (actor_user.get("email") or "").casefold():
            raise PermissionError("The staff directory record is not active.")
        require_complaint_area_access(actor_user, complaint_id)


def scoped_complaints(user: dict, *, include_unassigned: bool = False, filters: dict | None = None) -> list[dict]:
    """Select operational complaints only from the caller's verified area scope."""
    if not user or user.get("role") not in STAFF_ROLES or not user.get("id"):
        raise PermissionError("An authenticated government staff account is required.")
    active = fetch_all("SELECT email,role,active,department FROM users WHERE id=?", (user.get("id"),))
    if (not active or not active[0]["active"] or active[0]["role"] not in STAFF_ROLES
            or active[0]["role"] != user.get("role")
            or active[0]["email"].casefold() != (user.get("email") or "").casefold()):
        raise PermissionError("The government session is no longer valid.")
    from .auth import get_active_user
    identity = get_active_user(user["id"])
    if not identity or identity["email"].casefold() != (user.get("email") or "").casefold():
        raise PermissionError("The staff directory record is not active.")
    scope = staff_area_scope(user["id"])
    clauses = []
    params = []
    for area in scope:
        if area["area_code"] == CITY_WIDE_AREA_CODE:
            clauses.append("(c.state_code=? AND c.city_code=?)")
            params.extend((area["state_code"], area["city_code"]))
        else:
            clauses.append("(c.state_code=? AND c.city_code=? AND c.area_code=?)")
            params.extend((area["state_code"], area["city_code"], area["area_code"]))
    if include_unassigned and is_geography_admin(user.get("email", "")):
        clauses.append("(c.state_code IS NULL OR c.city_code IS NULL OR c.area_code IS NULL)")
    if not clauses:
        return []
    where = ["c.is_active=1", "(" + " OR ".join(clauses) + ")"]
    filters = filters or {}
    for key, col in (("state_code", "c.state_code"), ("city_code", "c.city_code"), ("area_code", "c.area_code"),
                     ("channel", "c.channel"), ("language", "c.language")):
        value = filters.get(key)
        if value:
            where.append(f"{col}=?")
            params.append(value)
    demo_citywide = is_demo_citywide_user(user, scope)
    if user.get("role") == "operator" and not demo_citywide:
        department = active[0]["department"]
        if not department:
            raise PermissionError("This officer account has no assigned department.")
        final_owner = "NULLIF((SELECT l.final_dept FROM ledger l WHERE l.complaint_id=c.id " \
                      "AND l.final_dept IS NOT NULL ORDER BY l.id DESC LIMIT 1),'')"
        latest_top3 = "(SELECT s.top3 FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1)"
        latest_abstained = "(SELECT s.abstained FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1)"
        suggested_owner = (f"CASE WHEN COALESCE({latest_abstained},1)=0 AND json_valid({latest_top3}) "
                           f"THEN NULLIF(TRIM(json_extract({latest_top3},'$[0].dept')),'') END")
        # Unrouted / abstained cases stay in the operator's manual-review queue;
        # confidently routed work remains constrained to the operator's department.
        where.append(f"(COALESCE({final_owner},{suggested_owner})=? OR "
                     f"(COALESCE({latest_abstained},1)=1 AND COALESCE({final_owner},'')=''))")
        params.append(department)
    query = "SELECT c.* FROM complaints c WHERE " + " AND ".join(where) + " ORDER BY c.id DESC"
    rows = fetch_all(query, tuple(params))
    return rows


def scoped_complaint_ids(user: dict, *, include_unassigned: bool = False) -> list[int]:
    return [row["id"] for row in scoped_complaints(user, include_unassigned=include_unassigned)]


def scoped_ids_clause(user: dict, column: str = "complaint_id", *, include_unassigned: bool = False):
    ids = scoped_complaint_ids(user, include_unassigned=include_unassigned)
    if not ids:
        return "1=0", []
    return f"{column} IN ({','.join('?' for _ in ids)})", ids
