-- NagrikSetu schema. All demo data is SYNTHETIC.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS complaints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         TEXT NOT NULL,              -- shared by parent + sub-issues
    parent_id       INTEGER REFERENCES complaints(id),  -- NULL for the original complaint
    original_text   TEXT NOT NULL,              -- NEVER modified
    language        TEXT NOT NULL,              -- english | hindi | hinglish | uncertain
    normalized_text TEXT,                       -- separate field, Hinglish normalisation
    channel         TEXT DEFAULT 'portal',      -- portal | phone | social | in-person
    location        TEXT DEFAULT 'unknown',     -- never invented
    location_normalized TEXT,                    -- NFC, trimmed/collapsed whitespace, casefolded matching key
    issue           TEXT,
    requested_action TEXT,
    missing_fields  TEXT,                       -- comma-separated
    is_synthetic    INTEGER NOT NULL DEFAULT 1,
    is_active       INTEGER NOT NULL DEFAULT 1,
    submitted_by    INTEGER REFERENCES users(id),
    state_code      TEXT,
    state_name      TEXT,
    city_code       TEXT,
    city_name       TEXT,
    area_code       TEXT,
    area_name       TEXT,
    source_start    INTEGER,
    source_end      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A citizen-visible split remains a proposal until a reviewer confirms it.
CREATE TABLE IF NOT EXISTS split_proposals (
    parent_id      INTEGER PRIMARY KEY REFERENCES complaints(id),
    proposal_json  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'proposed'
                   CHECK(status IN ('proposed','confirmed','edited','merged_back')),
    lead_department TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    decided_by     TEXT,
    reason         TEXT
);

-- Geographic access requests and grants are events; never rewrite their history.
CREATE TABLE IF NOT EXISTS government_area_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    state_code  TEXT NOT NULL,
    city_code   TEXT NOT NULL,
    area_code   TEXT NOT NULL,
    event_type  TEXT NOT NULL CHECK(event_type IN ('requested','approved','rejected','revoked')),
    actor_email TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Staff may select the service areas they need for case work without waiting
-- for an administrator. This stores active self-selected scopes separately
-- from the administrator approval history above.
CREATE TABLE IF NOT EXISTS staff_selected_areas (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    state_code  TEXT NOT NULL,
    city_code   TEXT NOT NULL,
    area_code   TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    selected_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id,state_code,city_code,area_code)
);

CREATE INDEX IF NOT EXISTS idx_staff_selected_areas_user
ON staff_selected_areas(user_id,state_code,city_code,area_code);

CREATE INDEX IF NOT EXISTS idx_geo_events_user_area_id
ON government_area_events(user_id,state_code,city_code,area_code,id);

CREATE TRIGGER IF NOT EXISTS government_area_events_no_update
BEFORE UPDATE ON government_area_events
BEGIN SELECT RAISE(ABORT, 'geographic access history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS government_area_events_no_delete
BEFORE DELETE ON government_area_events
BEGIN SELECT RAISE(ABORT, 'geographic access history is append-only'); END;

CREATE TABLE IF NOT EXISTS suggestions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id  INTEGER NOT NULL REFERENCES complaints(id),
    method        TEXT NOT NULL,                -- baseline | tfidf_lr
    top3          TEXT NOT NULL,                -- JSON: [{dept, score}]
    evidence      TEXT,                         -- JSON: matched words / rules
    confidence    REAL,
    abstained     INTEGER NOT NULL DEFAULT 0,   -- 1 = manual queue
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only decision ledger (see triggers below)
CREATE TABLE IF NOT EXISTS ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id    INTEGER NOT NULL REFERENCES complaints(id),
    event_type      TEXT NOT NULL,   -- suggested | accept | edit | clarify | escalate | challenge | challenge_resolved | transfer
    actor           TEXT NOT NULL,   -- system | operator | reviewer | citizen
    suggested_dept  TEXT,
    final_dept      TEXT,
    reason          TEXT,            -- mandatory on edit / escalate / transfer
    reason_code     TEXT,            -- wrong_category | missing_info | overlapping_ownership | duplicate_report | other
    details         TEXT,            -- JSON
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS ledger_no_update
BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete
BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_automatic_final_actions
BEFORE INSERT ON ledger
WHEN lower(trim(NEW.event_type)) IN (
    'close', 'closed', 'closure', 'reject', 'rejected', 'rejection',
    'deprioritize', 'deprioritise', 'auto_close', 'auto_reject',
    'auto_deprioritize', 'auto_deprioritise', 'auto_merge', 'merge',
    'duplicate_merge', 'auto_duplicate_merge'
)
BEGIN SELECT RAISE(ABORT, 'automatic complaint final action is prohibited'); END;

CREATE TRIGGER IF NOT EXISTS ledger_extraction_correction_requires_reason
BEFORE INSERT ON ledger
WHEN lower(trim(NEW.event_type))='extraction_corrected' AND length(trim(COALESCE(NEW.reason,'')))=0
BEGIN SELECT RAISE(ABORT, 'extraction corrections require a reason'); END;

CREATE TRIGGER IF NOT EXISTS ledger_split_edit_requires_reason
BEFORE INSERT ON ledger
WHEN lower(trim(NEW.event_type)) IN ('split_edited','split_merged_back')
 AND length(trim(COALESCE(NEW.reason,'')))=0
BEGIN SELECT RAISE(ABORT, 'split edits and merge-back require a reason'); END;

CREATE TRIGGER IF NOT EXISTS ledger_duplicate_decision_requires_reason
BEFORE INSERT ON ledger
WHEN lower(trim(NEW.event_type)) IN ('duplicate_confirmed','duplicate_rejected','duplicate_unlinked')
 AND length(trim(COALESCE(NEW.reason,'')))=0
BEGIN SELECT RAISE(ABORT, 'duplicate decisions require a reason'); END;

-- Sidecar chain preserves the original append-only ledger rows. Hashes are
-- generated atomically by a connection-registered SQLite function.
CREATE TABLE IF NOT EXISTS ledger_integrity (
    ledger_id       INTEGER PRIMARY KEY REFERENCES ledger(id),
    previous_hash   TEXT NOT NULL,
    event_hash      TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS ledger_integrity_after_insert
AFTER INSERT ON ledger
BEGIN
    INSERT INTO ledger_integrity (ledger_id, previous_hash, event_hash)
    VALUES (
        NEW.id,
        COALESCE((SELECT event_hash FROM ledger_integrity ORDER BY ledger_id DESC LIMIT 1),
                 'NYAYAFLOW_LEDGER_GENESIS_V1'),
        nyaya_ledger_hash(
            COALESCE((SELECT event_hash FROM ledger_integrity ORDER BY ledger_id DESC LIMIT 1),
                     'NYAYAFLOW_LEDGER_GENESIS_V1'),
            NEW.id, NEW.complaint_id, NEW.event_type, NEW.actor, NEW.suggested_dept,
            NEW.final_dept, NEW.reason, NEW.reason_code, NEW.details, NEW.created_at
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS ledger_integrity_no_update
BEFORE UPDATE ON ledger_integrity
BEGIN SELECT RAISE(ABORT, 'ledger integrity history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_integrity_no_delete
BEFORE DELETE ON ledger_integrity
BEGIN SELECT RAISE(ABORT, 'ledger integrity history is append-only'); END;

CREATE TABLE IF NOT EXISTS challenges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id  INTEGER NOT NULL REFERENCES complaints(id),
    raised_by     TEXT NOT NULL,             -- staff | citizen
    reason        TEXT NOT NULL,
    reason_category TEXT NOT NULL DEFAULT 'other' CHECK(reason_category IN
                    ('wrong_department','multiple_issues','missing_context','other')),
    assigned_reviewer TEXT,
    status        TEXT NOT NULL DEFAULT 'open',   -- open | resolved
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_by   TEXT,
    resolution    TEXT,
    outcome       TEXT,
    resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    filename     TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size    INTEGER NOT NULL,
    is_encrypted INTEGER NOT NULL DEFAULT 1,
    original_name TEXT,
    mime          TEXT,
    size_bytes    INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Candidate decisions and incident memberships are separate so every decision
-- remains auditable while an explicit unlink can deactivate a prior membership.
CREATE TABLE IF NOT EXISTS duplicate_reviews (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id          INTEGER NOT NULL REFERENCES complaints(id),
    candidate_complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    decision              TEXT NOT NULL,
    actor                 TEXT NOT NULL,
    reason                TEXT NOT NULL,
    reason_code           TEXT NOT NULL DEFAULT 'legacy_reason',
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS duplicate_reviews_no_update
BEFORE UPDATE ON duplicate_reviews
BEGIN SELECT RAISE(ABORT, 'duplicate review history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS duplicate_reviews_no_delete
BEFORE DELETE ON duplicate_reviews
BEGIN SELECT RAISE(ABORT, 'duplicate review history is append-only'); END;

CREATE TABLE IF NOT EXISTS incident_links (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id          INTEGER NOT NULL REFERENCES complaints(id),
    candidate_complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    incident_id           TEXT NOT NULL,
    active                INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_duplicate_reviews_pair
ON duplicate_reviews(complaint_id,candidate_complaint_id,id);
CREATE INDEX IF NOT EXISTS idx_incident_links_pair
ON incident_links(complaint_id,candidate_complaint_id,active,id);

CREATE TRIGGER IF NOT EXISTS incident_links_no_update
BEFORE UPDATE ON incident_links
BEGIN SELECT RAISE(ABORT, 'incident-link history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS incident_links_no_delete
BEFORE DELETE ON incident_links
BEGIN SELECT RAISE(ABORT, 'incident-link history is append-only'); END;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name            TEXT NOT NULL,
    password_hash   TEXT,
    auth_provider   TEXT NOT NULL CHECK(auth_provider IN ('password','google','legacy')),
    role            TEXT NOT NULL CHECK(role IN ('public','government','operator','reviewer','auditor')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    phone           TEXT UNIQUE,
    phone_verified  INTEGER NOT NULL DEFAULT 0,
    department      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS staff_directory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_email   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('operator','reviewer','auditor')),
    department    TEXT NOT NULL CHECK(department IN ('Roads Dept','Drainage Dept','Sanitation Dept',
                     'Electricity Dept','Water Supply Dept','Grievance Cell')),
    status        TEXT NOT NULL CHECK(status IN ('active','disabled')),
    registered    INTEGER NOT NULL DEFAULT 0 CHECK(registered IN (0,1)),
    tag           TEXT NOT NULL CHECK(tag='SYNTHETIC')
);

CREATE TABLE IF NOT EXISTS verification_challenges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose         TEXT NOT NULL CHECK(purpose IN ('staff_email','public_phone')),
    identifier_hash TEXT NOT NULL,
    code_salt       TEXT NOT NULL,
    code_hash       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    consumed_at     TEXT,
    last_sent_at    TEXT NOT NULL,
    request_count   INTEGER NOT NULL DEFAULT 1,
    window_started_at TEXT NOT NULL,
    account_name    TEXT,
    password_hash   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS verification_challenges_lookup
ON verification_challenges(purpose,identifier_hash,id DESC);

CREATE TABLE IF NOT EXISTS auth_login_attempts (
    identifier_hash TEXT PRIMARY KEY,
    window_started_at TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT
);

CREATE TABLE IF NOT EXISTS auth_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    identifier_hash TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    role            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS auth_events_no_update
BEFORE UPDATE ON auth_events
BEGIN SELECT RAISE(ABORT, 'auth event history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS auth_events_no_delete
BEFORE DELETE ON auth_events
BEGIN SELECT RAISE(ABORT, 'auth event history is append-only'); END;

-- Old username-based records are retained intact here during the account migration.
CREATE TABLE IF NOT EXISTS legacy_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('operator','reviewer','auditor')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_attempts (
    email           TEXT PRIMARY KEY COLLATE NOCASE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT
);

-- Corrections saved as labelled examples (NO automatic retraining)
CREATE TABLE IF NOT EXISTS corrections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    text         TEXT NOT NULL,
    wrong_dept   TEXT,
    correct_dept TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    suggested_dept TEXT,
    reason       TEXT,
    reason_code  TEXT,
    label_source TEXT NOT NULL DEFAULT 'reviewer_correction',
    is_verified  INTEGER NOT NULL DEFAULT 0,
    verified_by  TEXT
);

CREATE TABLE IF NOT EXISTS safety_rubric_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     INTEGER NOT NULL UNIQUE,
    status      TEXT NOT NULL,
    rules_json  TEXT NOT NULL,
    saved_by    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS safety_rubric_versions_no_update
BEFORE UPDATE ON safety_rubric_versions
BEGIN SELECT RAISE(ABORT, 'safety rubric version history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS safety_rubric_versions_no_delete
BEFORE DELETE ON safety_rubric_versions
BEGIN SELECT RAISE(ABORT, 'safety rubric version history is append-only'); END;

CREATE TABLE IF NOT EXISTS correction_reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    correction_id INTEGER NOT NULL REFERENCES corrections(id),
    reviewer      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK(status IN ('verified','rejected')),
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS correction_reviews_no_update
BEFORE UPDATE ON correction_reviews
BEGIN SELECT RAISE(ABORT, 'correction review history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS correction_reviews_no_delete
BEFORE DELETE ON correction_reviews
BEGIN SELECT RAISE(ABORT, 'correction review history is append-only'); END;
