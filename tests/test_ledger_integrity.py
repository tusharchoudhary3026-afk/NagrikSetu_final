import shutil
import sqlite3
from pathlib import Path

import pytest


def _make_ledger_event(db):
    from core.db import insert_complaint
    from core.ledger import log_event

    complaint_id = insert_complaint({
        "case_id": "CASE-HASH-CHAIN", "parent_id": None,
        "original_text": "The streetlight is broken at Gole Ka Mandir.",
        "language": "english", "normalized_text": "streetlight broken gole ka mandir",
        "channel": "portal", "location": "Gole Ka Mandir", "issue": "Electricity",
        "requested_action": None, "missing_fields": None, "is_synthetic": 1,
    })
    log_event(complaint_id, "suggested", "system", suggested_dept="Electricity Dept",
              details={"evidence": ["streetlight"]})
    return complaint_id


def test_legacy_ledger_rows_are_backfilled_once_and_verified(db, staff_user):
    from core.db import fetch_all, get_conn, init_db
    from core.ledger_integrity import verify_ledger_integrity

    complaint_id = _make_ledger_event(db)
    auditor = staff_user("auditor")
    original = fetch_all("SELECT * FROM ledger_integrity ORDER BY ledger_id")
    assert len(original) == 1

    # Simulate a pre-hash database copy containing ledger rows only.
    conn = get_conn()
    try:
        for trigger in ("ledger_integrity_after_insert", "ledger_integrity_no_update",
                        "ledger_integrity_no_delete"):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP TABLE ledger_integrity")
        conn.commit()
    finally:
        conn.close()

    init_db()
    first_backfill = fetch_all("SELECT * FROM ledger_integrity ORDER BY ledger_id")
    init_db()
    assert fetch_all("SELECT * FROM ledger_integrity ORDER BY ledger_id") == first_backfill
    assert first_backfill[0]["ledger_id"] == fetch_all(
        "SELECT id FROM ledger WHERE complaint_id=?", (complaint_id,))[0]["id"]
    assert verify_ledger_integrity(auditor) == {
        "valid": True, "checked": 1, "total": 1, "first_invalid_id": None, "problem": None}

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE ledger_integrity SET event_hash='changed' WHERE ledger_id=?",
                   (first_backfill[0]["ledger_id"],))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM ledger_integrity WHERE ledger_id=?", (first_backfill[0]["ledger_id"],))


def test_ledger_integrity_verification_reports_first_tampered_event(db, staff_user, monkeypatch, tmp_path):
    from core.db import DB_PATH, fetch_all
    from core import db as db_module
    from core.ledger_integrity import verify_ledger_integrity

    complaint_id = _make_ledger_event(db)
    auditor = staff_user("auditor")
    event_id = fetch_all("SELECT id FROM ledger WHERE complaint_id=?", (complaint_id,))[0]["id"]
    assert verify_ledger_integrity(auditor)["valid"] is True

    tampered = tmp_path / "tampered-copy.sqlite3"
    shutil.copy2(DB_PATH, tampered)
    conn = sqlite3.connect(tampered)
    try:
        conn.execute("DROP TRIGGER ledger_no_update")
        conn.execute("UPDATE ledger SET reason='changed in forensic copy' WHERE id=?", (event_id,))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", tampered)
    result = verify_ledger_integrity(auditor)
    assert result["valid"] is False
    assert result["first_invalid_id"] == event_id
    assert result["checked"] == 0


def test_ledger_integrity_is_auditor_only_and_visible_in_staff_page(db, staff_user):
    from core.ledger_integrity import verify_ledger_integrity
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    operator = staff_user("operator", "Roads Dept")
    with pytest.raises(PermissionError, match="not authorized"):
        verify_ledger_integrity(operator)

    auditor = staff_user("auditor")
    complaint_id = _make_ledger_event(db)
    # A photo record used to make auditor replay call the protected attachment
    # service despite the role being forbidden from viewing photo contents.
    db.execute("INSERT INTO attachments(complaint_id,filename,file_path,content_type,byte_size) "
               "VALUES(?,?,?,?,?)", (complaint_id, "private.png", "missing-private-file.png", "image/png", 10))
    root = Path(__file__).resolve().parent.parent
    app = AppTest.from_file(root / "app.py", default_timeout=30)
    app.session_state["authenticated_user"] = auditor
    app.run()
    app.switch_page("pages/7_Replay.py").run()
    assert not app.exception
    next(item for item in app.button if item.label == "Verify ledger integrity").click().run()
    assert not app.exception
    assert any("Integrity check passed: 1/1 events verified" in item.value for item in app.success)
