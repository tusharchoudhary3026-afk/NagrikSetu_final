"""Configurable high-consequence keyword flags; flags only request human attention."""
import json
import re
import csv
import sqlite3
import unicodedata

from .config import ROOT, SAFETY_TEST_CASES, SIM_LABEL

RUBRIC_PATH = ROOT / "data" / "safety_rules.json"
LEXICON_PATH = ROOT / "data" / "safety_lexicon.json"


def _lexicon():
    data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    if not isinstance(data.get("rules"), list):
        raise ValueError("Safety lexicon must define a list of rules.")
    return data


def _normalize(text):
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = re.sub(r"[\u200b\ufeff]", "", value)
    value = re.sub(r"[.!?;।॥]+", " sentencebreak ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _has_phrase(text, phrases):
    return any(re.search(r"(?<!\w)" + re.escape(_normalize(phrase)) + r"(?!\w)", text)
               for phrase in phrases if _normalize(phrase))


_SAFETY_CONCEPTS = {
    "spark": ("spark", "sparks", "sparking", "chingari", "arcing"),
    "fire": ("fire", "flames", "aag", "आग"),
    "smoke": ("smoke", "dhuan", "धुआं", "धुआँ"),
    "gas": ("gas", "lpg", "cylinder", "गैस"),
    "current": ("current", "shock", "karant", "bijli", "electric", "तार", "बिजली"),
    "wire": ("wire", "cable", "taar", "तार"),
    "manhole": ("manhole", "sewer", "drain", "naali", "नाली", "मैनहोल"),
    "road": ("road", "pothole", "sinkhole", "sadak", "सड़क", "gaddha", "गड्ढा"),
    "structure": ("wall", "building", "ceiling", "roof", "pillar", "deewar", "chhat", "दीवार", "छत"),
}


def _concepts(text):
    normalized = _normalize(text)
    found = set()
    for concept, forms in _SAFETY_CONCEPTS.items():
        if any(re.search(r"(?<!\w)" + re.escape(_normalize(form)) + r"(?!\w)", normalized)
               for form in forms):
            found.add(concept)
    return found


def _negates_hazard(context, hazard_phrase, negation_phrases):
    target_concepts = _concepts(hazard_phrase)
    for phrase in negation_phrases:
        if not _has_phrase(context, [phrase]):
            continue
        cue_concepts = _concepts(phrase)
        # A negation suppresses only the kind of evidence it actually denies.
        # For example, "no sparks" does not make an exposed wire disappear.
        if target_concepts & cue_concepts:
            return True
    return False


def _has_context_phrase(text, phrases, hazard_phrases, independent=False):
    hazards = [_normalize(phrase) for phrase in hazard_phrases if _normalize(phrase)]
    hazard_spans = []
    for hazard in hazards:
        hazard_spans.extend((match.start(), match.end(), hazard)
                            for match in re.finditer(r"(?<!\w)" + re.escape(hazard) + r"(?!\w)", text))
    for phrase in phrases:
        needle = _normalize(phrase)
        if not needle:
            continue
        for match in re.finditer(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", text):
            if independent and any(needle in hazard for hazard in hazards):
                continue
            if any(max(0, max(start, match.start()) - min(end, match.end())) <= 72
                   for start, end, _ in hazard_spans):
                return True
    return False


def _source_evidence(original, phrase, occurrence=0):
    pieces = [re.escape(piece) for piece in str(phrase).split() if piece]
    if not pieces:
        return str(phrase)
    matches = list(re.finditer(r"(?<!\w)" + r"[\W_]+".join(pieces) + r"(?!\w)", original,
                               flags=re.IGNORECASE | re.UNICODE))
    return matches[min(occurrence, len(matches) - 1)].group(0) if matches else str(phrase)


def _clause_context(text, match):
    """Limit context to the clause containing a hazard mention."""
    separators = list(re.finditer(
        r"\b(?:sentencebreak|but|however|whereas|while|although|lekin|magar|par)\b", text))
    left = max((item.end() for item in separators if item.end() <= match.start()), default=0)
    right = min((item.start() for item in separators if item.start() >= match.end()), default=len(text))
    return text[left:right]


def assess_safety(text: str):
    """Return an explainable triage suggestion. It cannot certify that a place is safe."""
    original = str(text or "")
    normalized = _normalize(original)
    lexicon = _lexicon()
    hits = []
    for rule in lexicon["rules"]:
        active_hit = None
        suppressed_hit = None
        for phrase in sorted(rule.get("keywords", []), key=len, reverse=True):
            needle = _normalize(phrase)
            if not needle:
                continue
            matches = list(re.finditer(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized))
            for occurrence, match in enumerate(matches):
                context = _clause_context(normalized, match)
                # Context is checked around each hazard occurrence; a negation about
                # one hazard does not suppress unrelated hazards elsewhere.
                negated = _negates_hazard(context, phrase, lexicon.get("negation", []))
                resolved = _has_phrase(context, lexicon.get("resolution", []))
                historical = _has_phrase(context, lexicon.get("historical", []))
                if not (negated or resolved or historical):
                    active_hit = {"rule_id": rule["rule_id"], "category": rule["category"],
                                  "phrase": phrase, "evidence": _source_evidence(original, phrase, occurrence),
                                  "critical_trigger": bool(rule.get("critical_trigger")),
                                  "severity_weight": int(rule.get("severity_weight", 0)),
                                  "context_status": "possibly_active"}
                    break
                else:
                    status = "resolved" if resolved else "historical" if historical else "negated"
                    if suppressed_hit is None:
                        suppressed_hit = {"rule_id": rule["rule_id"], "category": rule["category"],
                                          "phrase": phrase, "evidence": phrase, "suppressed": status,
                                          "critical_trigger": bool(rule.get("critical_trigger")),
                                          "severity_weight": int(rule.get("severity_weight", 0)),
                                          "context_status": status}
            if active_hit:
                break
        if active_hit:
            hits.append(active_hit)
        elif suppressed_hit:
            hits.append(suppressed_hit)
    # Keep the existing reviewer-managed rubric operational alongside the
    # expanded phrase library. New rubric terms remain team-proposed signals.
    rubric = load_rubric()
    lexicon_terms = {_normalize(phrase) for rule in lexicon["rules"]
                     for phrase in rule.get("keywords", [])}
    for rule in rubric.get("rules", []):
        for phrase in rule.get("terms", []):
            needle = _normalize(phrase)
            if not needle or needle in lexicon_terms:
                continue
            match = re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized)
            if not match:
                continue
            context = _clause_context(normalized, match)
            negated = _negates_hazard(context, phrase, lexicon.get("negation", []))
            resolved = _has_phrase(context, lexicon.get("resolution", []))
            historical = _has_phrase(context, lexicon.get("historical", []))
            status = "resolved" if resolved else "historical" if historical else "negated" if negated else None
            hits.append({"rule_id": "CUSTOM_" + str(rule.get("id", "rule")),
                         "category": "configurable", "label": rule.get("label", "Team-proposed safety term"),
                         "phrase": phrase, "evidence": _source_evidence(original, phrase),
                         "critical_trigger": False, "severity_weight": 20,
                         "context_status": status or "possibly_active", "suppressed": status})
            break
    active = [hit for hit in hits if not hit.get("suppressed")]
    hazard_phrases = [hit["phrase"] for hit in active]
    danger_signal = bool(active) and _has_context_phrase(
        normalized, lexicon.get("danger_indicators", []), hazard_phrases)
    danger = bool(active) and _has_context_phrase(
        normalized, lexicon.get("danger_indicators", []), hazard_phrases, independent=True)
    immediate = bool(active) and _has_context_phrase(normalized, lexicon.get("immediate", []), hazard_phrases)
    exposure = bool(active) and _has_context_phrase(normalized, lexicon.get("exposure_indicators", []), hazard_phrases)
    supporting = bool(active) and _has_context_phrase(normalized, lexicon.get("supporting_indicators", []), hazard_phrases)
    weights = lexicon.get("weights", {})
    hazard_score = max([int(weights.get("hazard", 0)),
                        *(int(hit.get("severity_weight", 0)) for hit in active)]) if active else 0
    score = min(100, sum((int(value) for value in
                          (hazard_score, int(weights.get("danger", 0)) if danger else 0,
                           int(weights.get("immediate", 0)) if immediate else 0,
                           int(weights.get("exposure", 0)) if exposure else 0,
                           int(weights.get("supporting", 0)) if supporting else 0))))
    critical = any(hit["critical_trigger"] for hit in active) and (danger_signal or exposure)
    thresholds = lexicon.get("thresholds", {})
    if critical:
        severity = "urgent_review"
    elif score >= int(thresholds.get("urgent_review", 65)):
        severity = "urgent_review"
    elif score >= int(thresholds.get("priority_safety_review", 40)):
        severity = "priority_safety_review"
    elif score >= int(thresholds.get("attention_recommended", 20)):
        severity = "attention_recommended"
    else:
        severity = "normal_queue"
    uncertainty_present = _has_phrase(normalized, lexicon.get("uncertainty", []))
    unmapped_uncertainty = (not active and uncertainty_present and
                            _has_phrase(normalized, lexicon.get("uncertainty_triggers", [])))
    unclear = ((bool(active) and uncertainty_present and not danger_signal) or unmapped_uncertainty)
    if unclear:
        severity = "needs_human_verification"
    categories = list(dict.fromkeys(hit["category"] for hit in active))
    evidence = list(dict.fromkeys(hit["evidence"] for hit in active))
    reasons = []
    if active:
        reasons.append("Possible active municipal hazard matched configurable phrase rules")
    if danger_signal:
        reasons.append("Complaint text includes a danger indicator")
    if immediate:
        reasons.append("Complaint text indicates an immediate or current condition")
    if exposure:
        reasons.append("Complaint text mentions a public or higher-exposure location")
    if supporting:
        reasons.append("Additional supporting context is present")
    if critical:
        reasons.append("A configured critical-hazard override applies; reviewer verification is required")
    if unclear:
        reasons.append("Hazard wording is uncertain; ask for clarification or verify manually")
    suppressed = [hit for hit in hits if hit.get("suppressed")]
    context_status = ("unclear" if unclear else "possibly_active" if active else
                      suppressed[0]["context_status"] if suppressed else "no_hazard_evidence")
    flagged = (bool(active) and score >= int(thresholds.get("attention_recommended", 20))) or unclear
    return {"safety_flag": flagged, "flagged": flagged, "risk_score": score,
            "severity": severity, "hazard_categories": categories, "evidence": evidence,
            "reasons": reasons, "rule_ids": list(dict.fromkeys(hit["rule_id"] for hit in active)),
            "context_status": context_status, "requires_human_review": flagged or unclear,
            "critical_override": critical, "rule_version": lexicon.get("version", "v1"),
            "threshold_status": lexicon.get("status"), "hits": hits,
            "flags": [{"rule_id": hit["rule_id"], "label": hit.get("label", hit["category"].replace("_", " ").title()),
                       "matched_text": [hit["phrase"]], "status": "HUMAN_REVIEW_REQUIRED"}
                      for hit in active],
            "rubric_status": rubric.get("status", lexicon.get("status")),
            "rubric_version": rubric.get("version", lexicon.get("version", "v1")),
            "message": "Triage suggestion only; this system cannot certify that a location is safe."}


def record_safety_decision(complaint_id: int, actor_user: dict, decision: str,
                           reason: str, severity: str | None = None):
    """Append a reasoned reviewer decision without replacing the system assessment."""
    from .auth import authorize_user_action
    from .db import fetch_all
    from .ledger import log_event

    actor = authorize_user_action((actor_user or {}).get("id"), "can_review")
    allowed = {"confirm", "change_severity", "false_positive", "resolved", "request_information", "escalate"}
    if decision not in allowed:
        raise ValueError("Choose a supported safety review action.")
    reason = str(reason or "").strip()
    if not reason or len(reason) > 1000:
        raise ValueError("A reason of 1 to 1000 characters is required for this safety decision.")
    allowed_severities = {"normal_queue", "attention_recommended", "priority_safety_review", "urgent_review", "needs_human_verification"}
    if decision == "change_severity" and severity not in allowed_severities:
        raise ValueError("Choose a valid review severity.")
    row = fetch_all("SELECT original_text FROM complaints WHERE id=?", (complaint_id,))
    if not row:
        raise ValueError("Complaint was not found.")
    assessment = assess_safety(row[0]["original_text"])
    log_event(complaint_id, "safety_review_" + decision, actor.get("email", "reviewer"),
              reason=reason, actor_user=actor,
              details={"system_assessment": {key: assessment[key] for key in
                       ("risk_score", "severity", "rule_version", "rule_ids", "evidence")},
                       "decision": decision,
                       "reviewed_severity": severity if decision == "change_severity" else None,
                       "previous_value": {"severity": assessment["severity"],
                                          "safety_flag": assessment["safety_flag"]},
                       "updated_value": {"decision": decision,
                                         "severity": severity if decision == "change_severity" else assessment["severity"],
                                         "safety_flag": False if decision == "false_positive" else assessment["safety_flag"]},
                       "reviewer_id": actor.get("id")})
    return assessment


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
        predicted = assess_safety(case["text"])["safety_flag"]
        tp += int(expected and predicted)
        fn += int(expected and not predicted)
        fp += int(not expected and predicted)
        tn += int(not expected and not predicted)
        row = by_language.setdefault(case["language"], {"n": 0, "expected_positive": 0,
                                                          "true_positive": 0, "predicted_positive": 0,
                                                          "false_positive": 0, "false_negative": 0})
        row["n"] += 1
        row["expected_positive"] += int(expected)
        row["true_positive"] += int(expected and predicted)
        row["predicted_positive"] += int(predicted)
        row["false_positive"] += int(not expected and predicted)
        row["false_negative"] += int(expected and not predicted)
    positive = tp + fn
    for row in by_language.values():
        predicted = row["predicted_positive"]
        row["precision"] = row["true_positive"] / predicted if predicted else None
        row["recall"] = (row["true_positive"] / row["expected_positive"]
                         if row["expected_positive"] else None)
    return {"status_label": "SYNTHETIC RUBRIC SANITY CHECK",
            "total": len(cases), "true_positive": tp, "positive_denominator": positive,
            "recall": tp / positive if positive else None,
            "false_positive": fp, "negative_denominator": fp + tn,
            "precision_denominator": tp + fp,
            "precision": tp / (tp + fp) if tp + fp else None,
            "false_negative": fn, "true_negative": tn, "by_language": by_language,
            "evaluation_status": "synthetic_check_only",
            "real_world_evaluation": "pending independently reviewed municipal complaint labels",
            "limitation": "Only 12 synthetic cases are included; these results are not real-world precision, recall, or safety validation."}
