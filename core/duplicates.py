"""Duplicate/incident candidate support. Similarity is a review signal, never a verdict."""
import re
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from .auth import enforce_rbac
from .geo import authorize_staff_complaint, scoped_complaint_ids
from .geo import STAFF_ROLES
from .db import fetch_all, get_conn
from .intake import normalize_location
from .config import (DUP_ISSUE_WEIGHT, DUP_LOCATION_WEIGHT, DUP_MIN_SCORE,
                     DUP_TEXT_WEIGHT, DUP_TIME_WINDOW_DAYS)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_TOKEN = re.compile(r"[\w\u0900-\u097f]+", re.UNICODE)
_STOP = {"the", "and", "for", "near", "this", "that", "with", "from", "road", "at", "on",
         "has", "have", "been", "is", "are", "was", "were", "mein", "me", "hai", "hain",
         "pe", "par", "ki", "ka", "ke", "और", "है", "हैं", "में", "पर", "की", "का", "के"}
DECISIONS = {"same_incident", "related", "different", "needs_info", "unlink"}
DECISION_REASON_CODES = {
    "same_incident": "same_incident",
    "related": "related_not_duplicate",
    "different": "different_incident",
    "needs_info": "insufficient_information",
    "unlink": "reopen_after_review",
}
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "duplicate_threshold.json"
DEFAULT_CONFIG = {
    "threshold": DUP_MIN_SCORE,
    "time_window_days": DUP_TIME_WINDOW_DAYS,
    "weights": {"text": DUP_TEXT_WEIGHT, "issue": DUP_ISSUE_WEIGHT,
                "location": DUP_LOCATION_WEIGHT},
}


def _tokens(text):
    return {word for word in _TOKEN.findall((text or "").casefold()) if len(word) > 2 and word not in _STOP}


def _labels(value):
    if not value or str(value).strip().casefold() in {"unknown", "none", "nan"}:
        return set()
    return {part.strip().casefold() for part in re.split(r"[/,;|]", str(value)) if part.strip()}


def _location_tokens(value):
    normalized = normalize_location(value) or "unknown"
    if normalized.casefold() in {"unknown", ""}:
        return set()
    return {token for token in _tokens(normalized) if token not in {"ward", "gwalior", "madhya", "pradesh"}}


def _location_relation(location_a, location_b):
    a, b = normalize_location(location_a) or "unknown", normalize_location(location_b) or "unknown"
    if a.casefold() in {"unknown", ""} or b.casefold() in {"unknown", ""}:
        return "unknown"
    if a.casefold() == b.casefold():
        return "same"
    return "nearby" if _location_tokens(a) & _location_tokens(b) else "different"


def _tfidf_similarity(text_a, text_b):
    vectorizer = TfidfVectorizer(token_pattern=r"(?u)[\w\u0900-\u097f]+", ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform([text_a or "", text_b or ""])
        return float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
    except ValueError:
        return 0.0


def _tfidf_scores(texts):
    """Fit once over the visible complaint corpus and return target-to-row cosines."""
    vectorizer = TfidfVectorizer(token_pattern=r"(?u)[\w\u0900-\u097f]+", ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform([text or "" for text in texts])
        return cosine_similarity(matrix[0:1], matrix[1:]).ravel().tolist()
    except ValueError:
        return [0.0] * max(0, len(texts) - 1)


def grounded_evidence(text_a, text_b, issue_a="unknown", issue_b="unknown",
                      location_a="unknown", location_b="unknown"):
    """Only return terms/labels that literally occur in both corresponding fields."""
    return {
        "shared_terms": sorted(_tokens(text_a) & _tokens(text_b))[:12],
        "shared_issue_labels": sorted(_labels(issue_a) & _labels(issue_b)),
        "shared_location_terms": sorted(_location_tokens(location_a) & _location_tokens(location_b)),
    }


def _date(value):
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def load_duplicate_config():
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    weights = config.get("weights", {})
    if (set(weights) not in ({"text", "location", "time"}, {"text", "issue", "location"})
            or any(float(value) < 0 for value in weights.values())):
        raise ValueError("Duplicate score config must contain valid non-negative candidate weights.")
    if abs(sum(float(value) for value in weights.values()) - 1) > 1e-6:
        raise ValueError("Duplicate score weights must sum to 1.")
    if not 0 <= float(config.get("threshold", -1)) <= 1:
        raise ValueError("Duplicate match threshold must be between 0 and 1.")
    return config


def _asset_markers(text):
    markers = {}
    pattern = re.compile(r"\b(pole|lane|gali|ward|asset)\s*(?:no\.?|number|#)?\s*(\d+)\b", re.I)
    for kind, number in pattern.findall(text or ""):
        markers.setdefault(kind.casefold(), set()).add(number)
    return markers


def compare_pair(text_a, text_b, location_a="unknown", location_b="unknown",
                 created_a=None, created_b=None, issue_a="unknown", issue_b="unknown",
                 time_window_days=None, text_similarity=None):
    """Return transparent candidate signals. The result is never a verdict."""
    config = load_duplicate_config()
    window = config.get("time_window_days", 7) if time_window_days is None else time_window_days
    if window < 0:
        raise ValueError("The candidate time window cannot be negative.")
    terms = grounded_evidence(text_a, text_b, issue_a, issue_b, location_a, location_b)
    shared = terms["shared_terms"]
    text_score = _tfidf_similarity(text_a, text_b) if text_similarity is None else float(text_similarity)
    same_issue = bool(terms["shared_issue_labels"])
    loc_a = normalize_location(location_a) or "unknown"
    loc_b = normalize_location(location_b) or "unknown"
    location_known = loc_a.casefold() not in {"", "unknown"} and loc_b.casefold() not in {"", "unknown"}
    location_relation = _location_relation(loc_a, loc_b)
    location_match = location_relation == "same"
    date_a, date_b = _date(created_a), _date(created_b)
    days = abs((date_a - date_b).total_seconds()) / 86400 if date_a and date_b else None
    time_match = days is not None and days <= window
    score = (DUP_TEXT_WEIGHT * text_score + DUP_ISSUE_WEIGHT * float(same_issue)
             + DUP_LOCATION_WEIGHT * float(location_match))
    conflicts = []
    markers_a, markers_b = _asset_markers(text_a), _asset_markers(text_b)
    different_asset = any(markers_a[key].isdisjoint(markers_b[key])
                          for key in markers_a.keys() & markers_b.keys())
    if different_asset:
        conflicts.append("different numbered asset, ward, or lane")
        score *= 0.15
    if location_relation == "different":
        conflicts.append("different location")
    if location_relation == "nearby":
        conflicts.append("nearby location only; verify exact place")
    if _labels(issue_a) and _labels(issue_b) and not same_issue:
        conflicts.append("same place, different issue" if location_relation in {"same", "nearby"}
                         else "different extracted issue")
    if days is not None and days > window:
        conflicts.append("outside configured time window")
    if text_score < 0.15:
        conflicts.append("low text similarity; may be a different incident")
    if ("different extracted issue" in conflicts or "same place, different issue" in conflicts
            or "outside configured time window" in conflicts):
        score *= 0.35
    if different_asset:
        score *= 0.15
    missing = []
    if not location_known:
        missing.append("location")
    if days is None:
        missing.append("time")
    return {
        "score": round(score, 4), "text_score": round(text_score, 4),
        "text_sim": round(text_score, 4), "same_issue": same_issue,
        "shared_issue_labels": terms["shared_issue_labels"],
        "shared_location_terms": terms["shared_location_terms"],
        "location": location_relation, "location_match": bool(location_match),
        "time_difference_days": round(days, 2) if days is not None else None,
        "time_match": bool(time_match), "shared_terms": shared[:12],
        "conflicts": conflicts, "missing_fields": missing,
        "score_kind": "candidate_score_not_probability",
        "hard_conflict": bool(different_asset or "different extracted issue" in conflicts),
    }


def candidate_outcome(evidence, config=None):
    """Describe a review candidate; this label never links or suppresses a case."""
    config = config or load_duplicate_config()
    threshold = float(config["threshold"])
    score = float(evidence.get("score", 0.0))
    guarded = bool(evidence.get("guard_flags") or evidence.get("hard_conflict"))
    if score >= threshold and not guarded:
        return "likely same"
    if evidence.get("hard_conflict"):
        return "distinct"
    if score >= threshold * 0.55:
        return "related"
    if evidence.get("missing_fields"):
        return "insufficient evidence"
    return "distinct"


def _latest_decision(a, b):
    rows = fetch_all(
        "SELECT decision FROM duplicate_reviews WHERE "
        "(complaint_id=? AND candidate_complaint_id=?) OR "
        "(complaint_id=? AND candidate_complaint_id=?) ORDER BY id DESC LIMIT 1",
        (a, b, b, a))
    return rows[0]["decision"] if rows else "needs_review"


def _latest_link(a, b):
    rows = fetch_all("SELECT incident_id,active FROM incident_links WHERE "
                     "(complaint_id=? AND candidate_complaint_id=?) OR "
                     "(complaint_id=? AND candidate_complaint_id=?) ORDER BY id DESC LIMIT 1",
                     (a, b, b, a))
    return rows[0] if rows else {"incident_id": None, "active": 0}


def find_candidates(complaint_id: int, limit: int = 10, time_window_days: int | None = None,
                    actor_user: dict | None = None):
    config = load_duplicate_config()
    window = config.get("time_window_days", 7) if time_window_days is None else time_window_days
    if window < 0:
        raise ValueError("The candidate time window cannot be negative.")
    permitted = None
    if actor_user and actor_user.get("role") in STAFF_ROLES:
        authorize_staff_complaint(actor_user["role"], complaint_id, actor_user)
        permitted = set(scoped_complaint_ids(actor_user, include_unassigned=True))
        if int(complaint_id) not in permitted:
            raise PermissionError("This complaint is outside your department or selected service areas.")
    source_rows = fetch_all("SELECT * FROM complaints WHERE id=? AND is_active=1", (complaint_id,))
    if not source_rows:
        raise ValueError("Complaint not found.")
    source = source_rows[0]
    # Split children are separately reviewable but do not become candidate parents.
    if source.get("parent_id") is not None:
        return []
    source_location = source.get("location_normalized") or normalize_location(source.get("location")) or "unknown"
    source_date = _date(source.get("created_at"))
    if permitted is None:
        rows = fetch_all("SELECT * FROM complaints WHERE id<>? AND is_active=1 AND parent_id IS NULL ORDER BY id DESC", (complaint_id,))
    else:
        other_ids = sorted(permitted - {int(complaint_id)})
        if not other_ids:
            rows = []
        else:
            marks = ",".join("?" for _ in other_ids)
            rows = fetch_all(f"SELECT * FROM complaints WHERE id<>? AND is_active=1 AND parent_id IS NULL AND id IN ({marks}) "
                             "ORDER BY id DESC", [complaint_id, *other_ids])
    candidates = []
    corpus_texts = [source.get("normalized_text") or source["original_text"]] + [
        row.get("normalized_text") or row["original_text"] for row in rows]
    similarities = _tfidf_scores(corpus_texts)
    for row, corpus_similarity in zip(rows, similarities):
        evidence = compare_pair(
            source.get("normalized_text") or source["original_text"],
            row.get("normalized_text") or row["original_text"],
            source_location,
            row.get("location_normalized") or normalize_location(row.get("location")) or "unknown",
            source.get("created_at"), row.get("created_at"),
            source.get("issue") or "unknown", row.get("issue") or "unknown", window,
            text_similarity=corpus_similarity)
        # Similarity uses normalized_text, while displayed terms must be present in the originals.
        evidence.update(grounded_evidence(
            source["original_text"], row["original_text"],
            source.get("issue") or "unknown", row.get("issue") or "unknown",
            source.get("location") or "unknown", row.get("location") or "unknown"))
        evidence["same_issue"] = bool(evidence["shared_issue_labels"])
        text_score = evidence["text_score"]
        # Suppress unrelated rows. Missing fields are shown if text itself is a candidate.
        if text_score == 0 and not evidence["same_issue"] and evidence["location"] not in {"same", "nearby"}:
            continue
        link = _latest_link(complaint_id, row["id"])
        evidence["guard_flags"] = list(evidence["conflicts"])
        candidates.append({
            "complaint_id": row["id"], "case_id": row["case_id"],
            "original_text": row["original_text"], "location": row.get("location") or "unknown",
            "created_at": row.get("created_at"), "language": row.get("language"),
            "channel": row.get("channel"), "issue": row.get("issue") or "unknown",
            "normalized_text": row.get("normalized_text") or row["original_text"],
            "text_score": round(text_score, 4),
            **evidence,
            "candidate_outcome": candidate_outcome(evidence, config),
            "decision": _latest_decision(complaint_id, row["id"]),
            "active_link": bool(link["active"]), "incident_id": link["incident_id"] if link["active"] else None,
        })
    candidates.sort(key=lambda item: (-item["score"], item["complaint_id"]))
    return candidates[:max(0, min(int(limit), 100))]


def candidates(complaint_id: int, top_k: int = 5, window_days: int | None = None,
              actor_user: dict | None = None):
    """Named interface for the ranked top-k candidate list used by staff review."""
    return find_candidates(complaint_id, limit=top_k, time_window_days=window_days,
                           actor_user=actor_user)


def record_decision(complaint_id: int, candidate_id: int, decision: str, actor_role: str,
                    reason: str, actor_email: str | None = None, reason_code: str | None = None,
                    actor_user: dict | None = None):
    enforce_rbac(actor_role, "can_review")
    authorize_staff_complaint(actor_role, complaint_id, actor_user)
    authorize_staff_complaint(actor_role, candidate_id, actor_user)
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of: {', '.join(sorted(DECISIONS))}.")
    if complaint_id == candidate_id:
        raise ValueError("A complaint cannot be linked to itself.")
    if not (reason or "").strip():
        raise ValueError("A reason is required for every duplicate decision.")
    expected_reason_code = DECISION_REASON_CODES[decision]
    if reason_code != expected_reason_code:
        raise ValueError(f"Decision '{decision}' requires reason code '{expected_reason_code}'.")
    actor = actor_email or (actor_user or {}).get("email") or actor_role
    ids = sorted((int(complaint_id), int(candidate_id)))
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id FROM complaints WHERE id IN (?,?)", ids).fetchall()
        if len(rows) != 2:
            raise ValueError("Both complaints must exist.")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO duplicate_reviews (complaint_id,candidate_complaint_id,decision,actor,reason,reason_code) "
        "VALUES (?,?,?,?,?,?)", (ids[0], ids[1], decision, actor,
                                      reason.strip(), reason_code))
        if decision in {"same_incident", "unlink"}:
            latest_link = conn.execute(
                "SELECT incident_id,active FROM incident_links WHERE complaint_id=? AND candidate_complaint_id=? "
                "ORDER BY id DESC LIMIT 1", ids).fetchone()
            if decision == "same_incident":
                if latest_link and latest_link["active"]:
                    incident_id = latest_link["incident_id"]
                else:
                    member = conn.execute(
                        "SELECT incident_id FROM incident_links WHERE active=1 AND "
                        "(complaint_id IN (?,?) OR candidate_complaint_id IN (?,?)) ORDER BY id LIMIT 1",
                        (*ids, *ids)).fetchone()
                    incident_id = member["incident_id"] if member else f"INC-{uuid.uuid4().hex[:12].upper()}"
                    conn.execute("INSERT INTO incident_links (complaint_id,candidate_complaint_id,incident_id,active) VALUES (?,?,?,1)",
                                 (*ids, incident_id))
                event = "duplicate_confirmed"
            else:
                if not latest_link or not latest_link["active"]:
                    raise ValueError("These complaints do not have an active incident link.")
                conn.execute("INSERT INTO incident_links (complaint_id,candidate_complaint_id,incident_id,active) VALUES (?,?,?,0)",
                             (*ids, latest_link["incident_id"]))
                event = "duplicate_unlinked"
            details = json.dumps({"candidate_id": ids[1], "decision": decision})
            for cid in ids:
                conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,reason,details) VALUES (?,?,?,?,?)",
                             (cid, event, actor, reason.strip(), details))
                if event == "duplicate_confirmed":
                    # Retain the historical event consumed by older replay clients.
                    conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,reason,details) VALUES (?,?,?,?,?)",
                                 (cid, "duplicate_linked", actor, reason.strip(), details))
        elif decision == "different":
            details = json.dumps({"candidate_id": ids[1], "decision": decision})
            for cid in ids:
                conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,reason,details) VALUES (?,?,?,?,?)",
                             (cid, "duplicate_rejected", actor, reason.strip(), details))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
