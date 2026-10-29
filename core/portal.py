"""Private citizen portal queries. Every lookup is anchored to the logged-in user."""
from .challenges import create_challenge
from .db import fetch_all


def list_own_complaints(user_id: int) -> list[dict]:
    return fetch_all(
        "SELECT c.id,c.case_id,c.original_text,c.language,c.location,c.issue,c.state_name,c.city_name,c.area_name,c.created_at, "
        "(SELECT s.top3 FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1) AS top3, "
        "(SELECT s.abstained FROM suggestions s WHERE s.complaint_id=c.id ORDER BY s.id DESC LIMIT 1) AS abstained, "
        "(SELECT l.event_type FROM ledger l WHERE l.complaint_id=c.id AND l.event_type IN "
        "('accept','edit','clarify','escalate','transfer') ORDER BY l.id DESC LIMIT 1) AS last_decision, "
        "(SELECT l.final_dept FROM ledger l WHERE l.complaint_id=c.id AND l.event_type IN "
        "('accept','edit','clarify','escalate','transfer') ORDER BY l.id DESC LIMIT 1) AS final_dept, "
        "(SELECT COUNT(*) FROM challenges h WHERE h.complaint_id=c.id AND h.status='open') AS open_requests "
        ", (SELECT h.status FROM challenges h WHERE h.complaint_id=c.id ORDER BY h.id DESC LIMIT 1) AS latest_review_status "
        ", (SELECT h.outcome FROM challenges h WHERE h.complaint_id=c.id ORDER BY h.id DESC LIMIT 1) AS latest_review_outcome "
        ", (SELECT h.resolution FROM challenges h WHERE h.complaint_id=c.id ORDER BY h.id DESC LIMIT 1) AS latest_review_resolution "
        ", (SELECT h.reason_category FROM challenges h WHERE h.complaint_id=c.id ORDER BY h.id DESC LIMIT 1) AS latest_review_reason_category "
        ", (SELECT h.created_at FROM challenges h WHERE h.complaint_id=c.id ORDER BY h.id DESC LIMIT 1) AS latest_review_submitted_at "
        ", (SELECT p.status FROM split_proposals p WHERE p.parent_id=c.id) AS split_status "
        ", (SELECT p.proposal_json FROM split_proposals p WHERE p.parent_id=c.id) AS split_proposal_json "
        "FROM complaints c WHERE c.submitted_by=? AND c.parent_id IS NULL AND c.is_active=1 ORDER BY c.id DESC",
        (user_id,))


def get_own_complaint(user_id: int, complaint_id: int) -> dict | None:
    rows = fetch_all("SELECT id,case_id,original_text FROM complaints "
                     "WHERE id=? AND submitted_by=? AND parent_id IS NULL AND is_active=1",
                     (complaint_id, user_id))
    return rows[0] if rows else None


def list_own_split_children(user_id: int, parent_id: int) -> list[dict]:
    """Return linked child issues only when the parent belongs to the resident."""
    if not get_own_complaint(user_id, parent_id):
        return []
    return fetch_all("SELECT id,original_text,issue,source_start,source_end,is_active FROM complaints "
                     "WHERE parent_id=? ORDER BY id", (parent_id,))


def request_own_review(user_id: int, complaint_id: int, reason: str, actor_email: str,
                       reason_category: str = "other") -> int:
    complaint = get_own_complaint(user_id, complaint_id)
    if not complaint:
        raise ValueError("That complaint is not available in your account.")
    return create_challenge(complaint_id, "citizen", reason, actor_email=actor_email,
                            citizen_user_id=user_id, reason_category=reason_category)
