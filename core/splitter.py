"""Engine 2: compound complaint splitter. Proposes only. A human confirms or merges back."""
import re
import json
from . import router_baseline
from . import ownership
from .db import get_conn
from .auth import enforce_rbac
from .intake import process
from .intake import normalize_location
from .suggestions import selected_department
from .geo import authorize_staff_complaint

CONNECTORS = re.compile(
    r",\s*(?:(?:and|plus)\s+)?|&\s*|;\s*|\n\s*(?:\d{1,2}[.)]\s*)?|।\s*|"
    r"\s+(?:and|or|plus|as\s+well\s+as|along\s+with|aur|ya|tatha|saath\s+(?:hi|mein)|"
    r"और|तथा|साथ\s+ही)\s+|"
    r"\.\s+(?=[A-Z\u0900-\u097F])",
    re.IGNORECASE,
)
LEADING_CONNECTOR = re.compile(r"^(?:(?:and|plus|also|aur|tatha|साथ ही|और|तथा)\s+)+", re.I)


def _candidate_segments(text: str) -> list[dict]:
    """Return trimmed source spans. Every snippet can be traced to original text."""
    spans = []
    start = 0
    for match in CONNECTORS.finditer(text):
        end = match.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and (text[end - 1].isspace() or text[end - 1] in ",;।"):
            end -= 1
        if start < end:
            spans.append({"start": start, "end": end, "text": text[start:end]})
        start = match.end()
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ",;।"):
        end -= 1
    if start < end:
        spans.append({"start": start, "end": end, "text": text[start:end]})

    # A connector may be followed by an orphaned numeral or word. Trim only
    # list prefixes/conjunctions while keeping offsets into the untouched text.
    cleaned = []
    for span in spans:
        start, end = span["start"], span["end"]
        prefix = re.match(r"\s*\d{1,2}[.)]\s*", text[start:end])
        if prefix:
            start += prefix.end()
        leading = LEADING_CONNECTOR.match(text[start:end])
        if leading:
            start += leading.end()
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            cleaned.append({"start": start, "end": end, "text": text[start:end]})
    return cleaned


def _analyse_segment(segment: dict, keywords: dict) -> dict:
    result = dict(segment)
    extracted = process(segment["text"])
    route = router_baseline.route(segment["text"], keywords)
    result.update({
        "issue": extracted["issue"],
        "issue_evidence": extracted.get("issue_evidence", []),
        "suggestion": route,
        "department": selected_department(route),
        "abstained": bool(route.get("abstained", True)),
        "ownership_conflicts": ownership.check_suggestions(
            [item["dept"] for item in route.get("top3", [])], segment["text"]),
    })
    return result


def _merge_segments(left: dict, right: dict, source: str) -> dict:
    start, end = left["start"], right["end"]
    merged = _analyse_segment({"start": start, "end": end, "text": source[start:end].strip()},
                              router_baseline.load_keywords())
    # Preserve exact source offsets even when trimming whitespace for analysis.
    merged["text"] = source[start:end].strip()
    return merged


def propose_split(text: str):
    if not isinstance(text, str) or not text.strip():
        return {"is_compound": False, "parts": [], "note": "empty or unclear complaint",
                "ownership_conflicts": []}
    candidates = _candidate_segments(text)
    if len(candidates) < 2:
        route = router_baseline.route(text)
        depts = [item["dept"] for item in route.get("top3", [])]
        return {"is_compound": False, "parts": [], "note": "single issue",
                "ownership_conflicts": ownership.check_suggestions(depts, text)}

    kw = router_baseline.load_keywords()
    analysed = [_analyse_segment(item, kw) for item in candidates]

    # A source clause with no issue evidence is context, not a new complaint.
    attached = []
    for part in analysed:
        if part["issue"] == "unknown" or not part["issue_evidence"]:
            if attached:
                previous = attached.pop()
                attached.append(_merge_segments(previous, part, text))
            else:
                attached.append(part)
            continue
        if attached and (attached[-1]["issue"] == "unknown" or
                         not attached[-1]["issue_evidence"]):
            orphan = attached.pop()
            part = _merge_segments(orphan, part, text)
        attached.append(part)
    analysed = attached

    def routing_group(part):
        if part.get("department"):
            return ("department", part["department"])
        owners = {(item["owner"], item["overlap_owner"])
                  for item in part.get("ownership_conflicts", [])}
        return ("ownership_conflict", tuple(sorted(owners))) if owners else None

    # Combine neighboring clauses for the same supported owner. Keep a single
    # issue intact when its clauses only add context or repeat the same owner.
    grouped = []
    for part in analysed:
        if grouped and routing_group(part) is not None and routing_group(grouped[-1]) == routing_group(part):
            grouped[-1] = _merge_segments(grouped[-1], part, text)
        else:
            grouped.append(part)

    all_depts = {item["dept"] for part in grouped
                 for item in (part.get("suggestion", {}).get("top3") or [])}
    conflicts = ownership.check_suggestions(sorted(all_depts), text)
    # A documented ownership-overlap pattern is one incident requiring review,
    # not evidence that a citizen reported two independent complaints.
    lower = text.casefold()
    burst_flooding = (re.search(r"\b(?:pipe|pipeline)\b.{0,50}\b(?:burst|leak)\b", lower) and
                      re.search(r"\b(?:flood|flooding|water is flowing)\b.{0,40}\broad\b", lower))
    if burst_flooding and conflicts:
        return {"is_compound": False, "parts": [],
                "note": "possible ownership overlap within one incident; kept intact for staff review",
                "ownership_conflicts": conflicts}

    # Do not split on connector count alone. Each sub-issue needs evidence and
    # either a supported owner or an explicit ownership conflict for staff review.
    supported = [part for part in grouped if part.get("issue_evidence") and routing_group(part)]
    groups = {routing_group(part) for part in supported}
    if len(supported) < 2 or len(groups) < 2:
        whole_route = router_baseline.route(text, kw)
        whole_depts = [item["dept"] for item in whole_route.get("top3", [])]
        conflicts = ownership.check_suggestions(whole_depts, text)
        note = ("possible multi-clause text but one supported owner; kept intact"
                if len(candidates) > 1 else "single issue")
        if any(part.get("issue") == "unknown" for part in grouped):
            note = "unclear clause; kept intact for staff review"
        return {"is_compound": False, "parts": [], "note": note,
                "ownership_conflicts": conflicts}

    return {"is_compound": True, "parts": supported,
            "note": "proposed split; needs human confirmation",
            "ownership_conflicts": conflicts}


def parent_route_for_submission(route: dict, split_proposal: dict | None) -> dict:
    """Keep a compound parent unassigned until staff confirms its sub-issues."""
    parent_route = dict(route or {})
    if split_proposal and split_proposal.get("is_compound"):
        parent_route["abstained"] = True
    return parent_route


def confirm_split(parent_id: int, case_id: str, parts: list[dict], actor_role: str,
                  actor_email: str | None = None, actor_user: dict | None = None,
                  reason: str | None = None, edited: bool = False) -> list[int]:
    """Persist human-confirmed child issues, keeping the original parent intact."""
    enforce_rbac(actor_role, "can_review")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        proposal = conn.execute("SELECT status FROM split_proposals WHERE parent_id=?", (parent_id,)).fetchone()
        if proposal and edited:
            if not (reason or "").strip():
                raise ValueError("A written reason is required when editing a proposed split.")
            conn.execute("INSERT INTO ledger(complaint_id,event_type,actor,reason,details) VALUES(?,?,?,?,?)",
                         (parent_id, "split_edited", actor_email or actor_role, reason.strip(),
                          json.dumps({"parts": parts}, ensure_ascii=False, sort_keys=True)))
        ids = persist_confirmed_split(conn, parent_id, case_id, parts, actor_role,
                                      actor_email=actor_email, actor_user=actor_user)
        if proposal:
            state = "edited" if edited else "confirmed"
            conn.execute("UPDATE split_proposals SET proposal_json=?,status=?,updated_at=datetime('now'),"
                         "decided_by=?,reason=? WHERE parent_id=?",
                         (json.dumps({"is_compound": True, "parts": parts}, ensure_ascii=False, sort_keys=True),
                          state, actor_email or actor_role, (reason or "").strip() or None, parent_id))
        conn.commit()
        return ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def persist_confirmed_split(conn, parent_id: int, case_id: str, parts: list[dict], actor_role: str,
                            actor_email: str | None = None, actor_user: dict | None = None,
                            write_parent_event: bool = True) -> list[int]:
    """Insert a confirmed split into the caller's transaction (never commits it)."""
    enforce_rbac(actor_role, "can_review")
    if len(parts) < 2 or any(not str(part.get("text", "")).strip() for part in parts):
        raise ValueError("A confirmed split needs at least two non-empty issues.")
    parent = conn.execute("SELECT * FROM complaints WHERE id=?", (parent_id,)).fetchone()
    if not parent:
        raise ValueError("Parent complaint not found.")
    authorize_staff_complaint(actor_role, parent_id, actor_user)
    active = conn.execute("SELECT id FROM complaints WHERE parent_id=? AND is_active=1", (parent_id,)).fetchall()
    if active:
        raise ValueError("This complaint already has an active split.")
    ids = []
    confirmed_parts = []
    for part in parts:
        text = str(part["text"]).strip()
        start, end = part.get("start"), part.get("end")
        if start is not None or end is not None:
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
                raise ValueError("Each proposed sub-issue needs a valid source span.")
            if parent["original_text"][start:end] != text:
                raise ValueError("A sub-issue must match its exact text span in the original complaint.")
        rec = process(text, channel=parent["channel"])
        if part.get("issue"):
            rec["issue"] = part["issue"]
        rec["case_id"], rec["parent_id"], rec["is_synthetic"] = case_id, parent_id, parent["is_synthetic"]
        cur = conn.execute(
            "INSERT INTO complaints (case_id,parent_id,original_text,language,normalized_text,channel,location,"
            "location_normalized,issue,requested_action,missing_fields,is_synthetic,submitted_by,state_code,"
            "state_name,city_code,city_name,area_code,area_name,source_start,source_end) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["case_id"], rec["parent_id"], rec["original_text"], rec["language"],
             rec["normalized_text"], rec["channel"], parent["location"], normalize_location(parent["location"]),
             rec["issue"], rec["requested_action"], rec["missing_fields"], rec["is_synthetic"],
             parent["submitted_by"], parent["state_code"], parent["state_name"], parent["city_code"],
             parent["city_name"], parent["area_code"], parent["area_name"], start, end))
        child_id = cur.lastrowid
        ids.append(child_id)
        result = part.get("suggestion") or router_baseline.route(text)
        confirmed_parts.append({"child_id": child_id, "text": text, "source_start": start,
                                "source_end": end, "issue": rec.get("issue"),
                                "suggestion": result})
        conn.execute("INSERT INTO suggestions (complaint_id,method,top3,evidence,confidence,abstained) VALUES (?,?,?,?,?,?)",
                     (child_id, "baseline", json.dumps(result.get("top3", [])),
                      json.dumps(result.get("evidence", {})), result.get("confidence", 0.0),
                      int(bool(result.get("abstained", True)))))
        conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,suggested_dept,details) VALUES (?,?,?,?,?)",
                     (child_id, "suggested", "system", selected_department(result),
                      json.dumps({"split_parent_id": parent_id, "abstained": bool(result.get("abstained", True)),
                                  "extracted_issue_evidence": rec.get("issue_evidence", [])})))
        from .safety import assess_safety
        safety = assess_safety(text)
        conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,details) VALUES (?,?,?,?)",
                     (child_id, "safety_assessment_generated", "system",
                      json.dumps({key: safety.get(key) for key in
                                  ("safety_flag", "risk_score", "severity", "hazard_categories", "evidence",
                                   "reasons", "rule_ids", "context_status", "requires_human_review",
                                   "critical_override", "rule_version", "threshold_status")},
                                 ensure_ascii=False, sort_keys=True)))
    if write_parent_event:
        conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,details) VALUES (?,?,?,?)",
                     (parent_id, "split_confirmed", actor_email or actor_role,
                      json.dumps({"child_ids": ids, "subissues": confirmed_parts}, ensure_ascii=False,
                                 sort_keys=True)))
        conn.execute("UPDATE split_proposals SET status='confirmed',updated_at=datetime('now'),decided_by=? "
                     "WHERE parent_id=? AND status IN ('proposed','edited')",
                     (actor_email or actor_role, parent_id))
    return ids


def merge_back(parent_id: int, actor_role: str, reason: str, actor_email: str | None = None,
               actor_user: dict | None = None) -> int:
    """Deactivate current child views but preserve all child records and ledger history."""
    enforce_rbac(actor_role, "can_review")
    if not (reason or "").strip():
        raise ValueError("A written reason is required to merge a split back.")
    authorize_staff_complaint(actor_role, parent_id, actor_user)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        children = conn.execute("SELECT id FROM complaints WHERE parent_id=? AND is_active=1 ORDER BY id",
                                (parent_id,)).fetchall()
        if not children:
            raise ValueError("No active split to merge back.")
        conn.executemany("UPDATE complaints SET is_active=0 WHERE id=?", [(row["id"],) for row in children])
        ids = [row["id"] for row in children]
        conn.execute("INSERT INTO ledger (complaint_id,event_type,actor,reason,details) VALUES (?,?,?,?,?)",
                     (parent_id, "split_merged_back", actor_email or actor_role, reason.strip(),
                      json.dumps({"child_ids": ids})))
        conn.execute("UPDATE split_proposals SET status='merged_back',updated_at=datetime('now'),"
                     "decided_by=?,reason=? WHERE parent_id=?",
                     (actor_email or actor_role, reason.strip(), parent_id))
        conn.commit()
        return len(ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
