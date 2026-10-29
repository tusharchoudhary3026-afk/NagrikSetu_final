"""Verification for the append-only, SHA-256-linked ledger sidecar."""
from .auth import authorize_user_action
from .db import LEDGER_GENESIS, LEDGER_HASH_FIELDS, fetch_all, ledger_event_hash


def verify_ledger_integrity(actor_user: dict) -> dict:
    """Check every ledger event and link; only active audit-capable users may run it."""
    if not actor_user or not actor_user.get("id"):
        raise PermissionError("An active auditor account is required.")
    authorize_user_action(actor_user["id"], "can_audit")
    rows = fetch_all(
        "SELECT l.id,l.complaint_id,l.event_type,l.actor,l.suggested_dept,l.final_dept,"
        "l.reason,l.reason_code,l.details,l.created_at,i.previous_hash,i.event_hash "
        "FROM ledger l LEFT JOIN ledger_integrity i ON i.ledger_id=l.id ORDER BY l.id")
    integrity_count = fetch_all("SELECT COUNT(*) AS n FROM ledger_integrity")[0]["n"]
    previous = LEDGER_GENESIS
    checked = 0
    for row in rows:
        if row["event_hash"] is None or row["previous_hash"] != previous:
            return {"valid": False, "checked": checked,
                    "total": len(rows), "first_invalid_id": row["id"],
                    "problem": "Missing hash or broken previous-event link."}
        values = [row[field] for field in LEDGER_HASH_FIELDS]
        calculated = ledger_event_hash(previous, *values)
        if calculated != row["event_hash"]:
            return {"valid": False, "checked": checked,
                    "total": len(rows), "first_invalid_id": row["id"],
                    "problem": "Event content does not match its recorded hash."}
        previous = row["event_hash"]
        checked += 1
    if int(integrity_count) != len(rows):
        return {"valid": False, "checked": len(rows), "total": len(rows),
                "first_invalid_id": None,
                "problem": "Ledger and integrity sidecar row counts do not match."}
    return {"valid": True, "checked": len(rows), "total": len(rows),
            "first_invalid_id": None, "problem": None}
