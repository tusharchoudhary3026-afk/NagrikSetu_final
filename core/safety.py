"""Configurable high-consequence keyword flags; flags only request human attention."""
import json
import re
import csv
import sqlite3

from .config import ROOT, SAFETY_TEST_CASES, SIM_LABEL

RUBRIC_PATH = ROOT / "data" / "safety_rules.json"


def load_rubric():
    try:
        from .db import fetch_all
        rows = fetch_all("SELECT version,status,rules_json FROM safety_rubric_versions ORDER BY version DESC LIMIT 1")
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).casefold():
            raise
        rows = []
    if rows:
        return {"version": rows[0]["version"], "status": rows[0]["status"],
                "rules": json.loads(rows[0]["rules_json"])}
    rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    if not isinstance(rubric.get("rules"), list):
        raise ValueError("Safety rubric must define a list of rules.")
    return rubric


def _validate_rules(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise ValueError("The rubric must contain between 1 and 100 rules.")
    seen = set()
    clean = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Each safety rule must be an object with an ID, label, and terms.")
        rule_id, label, terms = rule.get("id"), rule.get("label"), rule.get("terms")
        if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z0-9_]{1,60}", rule_id):
            raise ValueError("Rule IDs must use lowercase letters, digits, and underscores.")
        if rule_id in seen:
            raise ValueError("Rule IDs must be unique within a rubric version.")
        seen.add(rule_id)
        if not isinstance(label, str) or not label.strip() or len(label) > 160:
            raise ValueError("Each rule needs a label of 1 to 160 characters.")
        if not isinstance(terms, list) or not 1 <= len(terms) <= 100:
            raise ValueError("Each rule needs between 1 and 100 text terms.")
        normalized_terms = []
        for term in terms:
            if not isinstance(term, str) or not term.strip() or len(term) > 100:
                raise ValueError("Safety terms must be non-empty text of at most 100 characters.")
            if any(ord(char) < 32 and char not in "\t\r\n" for char in term):
                raise ValueError("Safety terms cannot contain control characters.")
            if term.casefold() not in {value.casefold() for value in normalized_terms}:
                normalized_terms.append(term)
        clean.append({"id": rule_id, "label": label.strip(), "terms": normalized_terms})
    return clean


def save_rubric_version(actor_user: dict, rules: list, reason: str) -> int:
    """Append a team-proposed rubric version; never rewrite the configured baseline."""
    from .auth import authorize_user_action
    from .db import get_conn

    if not actor_user or not actor_user.get("id"):
        raise PermissionError("An active reviewer account is required to edit the rubric.")
    actor = authorize_user_action(actor_user["id"], "can_manage_corrections")
    reason = (reason or "").strip()
    if not reason or len(reason) > 1000:
        raise ValueError("Enter why this team-proposed safety rubric version is changing.")
    clean_rules = _validate_rules(rules)
    rubric_status = "TEAM-PROPOSED - requires human review"
    try:
        baseline_version = int(load_rubric().get("version", 1))
    except (TypeError, ValueError):
        baseline_version = 1
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT MAX(version) AS version FROM safety_rubric_versions").fetchone()["version"]
        if previous is None:
            previous = baseline_version
        version = int(previous) + 1
        conn.execute("INSERT INTO safety_rubric_versions(version,status,rules_json,saved_by,reason) "
                     "VALUES(?,?,?,?,?)",
                     (version, rubric_status,
                      json.dumps(clean_rules, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                      actor["email"], reason))
        conn.commit()
        return version
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def flag_safety(text: str):
    normalized = (text or "").casefold()
    flags = []
    rubric = load_rubric()
    for rule in rubric["rules"]:
        matched = []
        for term in rule.get("terms", []):
            for occurrence in re.finditer(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", normalized):
                prefix = normalized[max(0, occurrence.start() - 40):occurrence.start()]
                if re.search(r"(?:\bno\b|\bnot\b|\bwithout\b|\bnahi\b|\bnahin\b|नहीं)(?:\s+\w+){0,2}\s*$", prefix):
                    continue
                matched.append(term)
                break
        if matched:
            flags.append({"rule_id": rule["id"], "label": rule["label"],
                          "matched_text": matched, "status": "HUMAN_REVIEW_REQUIRED"})
    return {"flagged": bool(flags), "flags": flags,
            "rubric_status": rubric.get("status", "TEAM-PROPOSED"),
            "rubric_version": rubric.get("version", 1),
            "message": "Safety flag is a request for human attention, not a risk or priority decision."}


def evaluate_safety_rubric():
    """Measure only the included synthetic rubric checks; this is not field performance."""
    with SAFETY_TEST_CASES.open("r", encoding="utf-8-sig", newline="") as stream:
        cases = list(csv.DictReader(stream))
    if not cases:
        raise ValueError("Safety test set is empty.")
    tp = fp = tn = fn = 0
    by_language = {}
    for case in cases:
        expected = case["expected_flag"] == "1"
        predicted = flag_safety(case["text"])["flagged"]
        tp += int(expected and predicted)
        fn += int(expected and not predicted)
        fp += int(not expected and predicted)
        tn += int(not expected and not predicted)
        row = by_language.setdefault(case["language"], {"n": 0, "expected_positive": 0, "true_positive": 0})
        row["n"] += 1
        row["expected_positive"] += int(expected)
        row["true_positive"] += int(expected and predicted)
    positive = tp + fn
    return {"status_label": "SYNTHETIC RUBRIC SANITY CHECK",
            "total": len(cases), "true_positive": tp, "positive_denominator": positive,
            "recall": tp / positive if positive else None,
            "false_positive": fp, "negative_denominator": fp + tn,
            "true_negative": tn, "by_language": by_language,
            "limitation": "Cases are synthetic and term-aligned to the team-proposed rubric; this is not real-world safety recall."}
