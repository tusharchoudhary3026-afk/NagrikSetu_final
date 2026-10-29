import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from .config import DB_PATH, ROOT, SCHEMA_PATH

LEDGER_GENESIS = "NYAYAFLOW_LEDGER_GENESIS_V1"
LEDGER_HASH_FIELDS = (
    "id", "complaint_id", "event_type", "actor", "suggested_dept", "final_dept",
    "reason", "reason_code", "details", "created_at",
)


def ledger_event_hash(previous_hash, *values):
    """Hash canonical ledger event content plus the preceding event hash."""
    if len(values) != len(LEDGER_HASH_FIELDS):
        raise ValueError("Ledger hash requires all event fields.")
    event = dict(zip(LEDGER_HASH_FIELDS, values))
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{previous_hash}\n{payload}".encode("utf-8")).hexdigest()


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("nyaya_ledger_hash", 11, ledger_event_hash, deterministic=True)
    return conn


def _backfill_ledger_integrity(conn):
    """Append a genesis-based chain for legacy rows once; never repair partial chains."""
    rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    if not rows:
        return
    existing = conn.execute("SELECT ledger_id,previous_hash,event_hash FROM ledger_integrity "
                            "ORDER BY ledger_id").fetchall()
    if existing:
        indexed = {int(row["ledger_id"]): row for row in existing}
        if any(int(row["id"]) not in indexed for row in rows):
            raise RuntimeError("Ledger integrity chain is incomplete; refusing to reconstruct missing hashes.")
        return
    previous = LEDGER_GENESIS
    for row in rows:
        values = [row[field] for field in LEDGER_HASH_FIELDS]
        event_hash = ledger_event_hash(previous, *values)
        conn.execute("INSERT INTO ledger_integrity(ledger_id,previous_hash,event_hash) VALUES(?,?,?)",
                     (row["id"], previous, event_hash))
        previous = event_hash


def init_db():
    conn = get_conn()
    try:
        # Preserve pre-email accounts intact. They must re-register by email to use
        # the new interfaces; old staff roles are never silently promoted.
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" in tables:
            user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            if "email" not in user_columns:
                if "legacy_users" in tables:
                    raise RuntimeError("Both legacy and old users tables exist; refusing ambiguous migration.")
                conn.execute("ALTER TABLE users RENAME TO legacy_users")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        duplicate_review_columns = {row["name"] for row in conn.execute("PRAGMA table_info(duplicate_reviews)")}
        if "reason_code" not in duplicate_review_columns:
            conn.execute("ALTER TABLE duplicate_reviews ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'legacy_reason'")
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "active" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        # Additive auth hardening; old accounts and password hashes remain intact.
        for name, declaration in {
            "phone": "TEXT", "phone_verified": "INTEGER NOT NULL DEFAULT 0", "department": "TEXT"
        }.items():
            if name not in user_columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_phone_unique ON users(phone) WHERE phone IS NOT NULL")
        complaint_columns = {row["name"] for row in conn.execute("PRAGMA table_info(complaints)")}
        for name, declaration in {
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "state_code": "TEXT", "state_name": "TEXT", "city_code": "TEXT",
            "city_name": "TEXT", "area_code": "TEXT", "area_name": "TEXT",
            "source_start": "INTEGER", "source_end": "INTEGER",
            "location_normalized": "TEXT",
        }.items():
            if name not in complaint_columns:
                conn.execute(f"ALTER TABLE complaints ADD COLUMN {name} {declaration}")
        from .intake import normalize_location
        for row in conn.execute("SELECT id,location FROM complaints WHERE location_normalized IS NULL").fetchall():
            conn.execute("UPDATE complaints SET location_normalized=? WHERE id=?",
                         (normalize_location(row["location"]), row["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_location_normalized "
                     "ON complaints(location_normalized)")
        if "submitted_by" not in complaint_columns:
            conn.execute("ALTER TABLE complaints ADD COLUMN submitted_by INTEGER REFERENCES users(id)")
        attachment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(attachments)")}
        if "is_encrypted" not in attachment_columns:
            # Existing attachment rows are plaintext and must not be mistaken for encrypted files.
            conn.execute("ALTER TABLE attachments ADD COLUMN is_encrypted INTEGER NOT NULL DEFAULT 0")
        for name, declaration in {"original_name": "TEXT", "mime": "TEXT", "size_bytes": "INTEGER"}.items():
            if name not in attachment_columns:
                conn.execute(f"ALTER TABLE attachments ADD COLUMN {name} {declaration}")
        conn.execute("UPDATE attachments SET original_name=filename WHERE original_name IS NULL")
        conn.execute("UPDATE attachments SET mime=content_type WHERE mime IS NULL")
        conn.execute("UPDATE attachments SET size_bytes=byte_size WHERE size_bytes IS NULL")
        # Additive migration for databases created by earlier prototype versions.
        challenge_columns = {row["name"] for row in conn.execute("PRAGMA table_info(challenges)")}
        for name, declaration in {
            "resolved_by": "TEXT", "resolution": "TEXT", "outcome": "TEXT", "resolved_at": "TEXT",
            "reason_category": "TEXT NOT NULL DEFAULT 'other'", "assigned_reviewer": "TEXT"
        }.items():
            if name not in challenge_columns:
                conn.execute(f"ALTER TABLE challenges ADD COLUMN {name} {declaration}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_open_queue "
                     "ON challenges(status,reason_category,created_at)")
        correction_columns = {row["name"] for row in conn.execute("PRAGMA table_info(corrections)")}
        for name, declaration in {
            "suggested_dept": "TEXT", "reason": "TEXT", "reason_code": "TEXT",
            "label_source": "TEXT NOT NULL DEFAULT 'reviewer_correction'",
            "is_verified": "INTEGER NOT NULL DEFAULT 0", "verified_by": "TEXT"
        }.items():
            if name not in correction_columns:
                conn.execute(f"ALTER TABLE corrections ADD COLUMN {name} {declaration}")
        _backfill_ledger_integrity(conn)
        conn.commit()
        # The pseudo directory is a one-time seed. INSERT OR IGNORE deliberately
        # preserves any existing row and its registered status across restarts.
        directory_path = ROOT / "data" / "staff_directory.csv"
        if directory_path.is_file():
            with directory_path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    email = (row.get("staff_email") or "").strip().lower()
                    if not email:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO staff_directory"
                        "(staff_email,full_name,role,department,status,registered,tag) VALUES(?,?,?,?,?,?,?)",
                        (email, (row.get("full_name") or "").strip(), (row.get("role") or "").strip().lower(),
                         (row.get("department") or "").strip(), (row.get("status") or "").strip().lower(),
                         int((row.get("registered") or "0").strip() == "1"),
                         (row.get("tag") or "SYNTHETIC").strip().upper()))
            conn.commit()
    finally:
        conn.close()


def insert_complaint(rec: dict, submitted_by: int | None = None) -> int:
    from .intake import normalize_location

    cols = ["case_id", "parent_id", "original_text", "language", "normalized_text",
            "channel", "location", "location_normalized", "issue", "requested_action", "missing_fields",
            "is_synthetic", "submitted_by",
            "state_code", "state_name", "city_code", "city_name", "area_code", "area_name"]
    vals = [rec.get(k) for k in cols]
    vals[cols.index("location_normalized")] = normalize_location(rec.get("location"))
    vals[cols.index("submitted_by")] = submitted_by if submitted_by is not None else rec.get("submitted_by")
    conn = get_conn()
    try:
        cur = conn.execute(
            f"INSERT INTO complaints ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_suggestion(complaint_id, method, top3, evidence, confidence, abstained):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO suggestions (complaint_id, method, top3, evidence, confidence, abstained) "
            "VALUES (?,?,?,?,?,?)",
            (complaint_id, method, json.dumps(top3), json.dumps(evidence), confidence, int(abstained)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_public_submission(rec: dict, route: dict, submitted_by: int | None = None,
                             split_proposal: dict | None = None) -> int:
    """Persist the complaint, its routing suggestion, and initial ledger event atomically."""
    import json
    from .intake import normalize_location
    from .suggestions import selected_department

    cols = ["case_id", "parent_id", "original_text", "language", "normalized_text",
            "channel", "location", "location_normalized", "issue", "requested_action", "missing_fields", "is_synthetic",
            "submitted_by", "state_code", "state_name", "city_code", "city_name", "area_code", "area_name"]
    vals = [rec.get(key) for key in cols]
    vals[cols.index("location_normalized")] = normalize_location(rec.get("location"))
    vals[cols.index("submitted_by")] = submitted_by if submitted_by is not None else rec.get("submitted_by")
    suggestion = selected_department(route)
    details = {"abstained": bool(route.get("abstained")),
               "confidence": route.get("confidence", 0), "synthetic": True,
               "extracted_issue_evidence": rec.get("issue_evidence", []),
               "extraction_corrections": rec.get("extraction_corrections", {})}
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            f"INSERT INTO complaints ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        complaint_id = cur.lastrowid
        conn.execute(
            "INSERT INTO suggestions (complaint_id, method, top3, evidence, confidence, abstained) "
            "VALUES (?,?,?,?,?,?)",
            (complaint_id, "baseline", json.dumps(route.get("top3") or []),
             json.dumps(route.get("evidence") or {}), route.get("confidence", 0),
             int(bool(route.get("abstained")))))
        conn.execute(
            "INSERT INTO ledger (complaint_id,event_type,actor,suggested_dept,details) VALUES(?,?,?,?,?)",
            (complaint_id, "suggested", "system", suggestion, json.dumps(details)))
        from .safety import assess_safety
        safety = assess_safety(rec.get("original_text") or "")
        conn.execute(
            "INSERT INTO ledger (complaint_id,event_type,actor,details) VALUES(?,?,?,?)",
            (complaint_id, "safety_assessment_generated", "system",
             json.dumps({key: safety.get(key) for key in
                         ("safety_flag", "risk_score", "severity", "hazard_categories", "evidence",
                          "reasons", "rule_ids", "context_status", "requires_human_review",
                          "critical_override", "rule_version", "threshold_status")}, ensure_ascii=False,
                        sort_keys=True)))
        if split_proposal and split_proposal.get("is_compound"):
            proposal_json = json.dumps(split_proposal, ensure_ascii=False, sort_keys=True)
            conn.execute(
                "INSERT INTO split_proposals(parent_id,proposal_json,status,lead_department) VALUES(?,?,?,?)",
                (complaint_id, proposal_json, "proposed", suggestion))
            conn.execute(
                "INSERT INTO ledger(complaint_id,event_type,actor,suggested_dept,details) VALUES(?,?,?,?,?)",
                (complaint_id, "split_proposed", "system", suggestion,
                 json.dumps({"status": "proposed", "synthetic": True,
                             "proposal": split_proposal}, ensure_ascii=False, sort_keys=True)))
        conn.commit()
        return complaint_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql, params=()):
    conn = get_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
