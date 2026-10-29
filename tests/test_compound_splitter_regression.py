from core import intake
from core.splitter import propose_split
from core.suggestions import selected_department


def _departments(text):
    proposal = propose_split(text)
    return proposal, [selected_department(part["suggestion"]) for part in proposal["parts"]]


def test_reported_compound_input_proposes_two_grounded_spans_in_order():
    text = "road has a pothole, and the streetlight is also not working in ward 3"
    proposal, departments = _departments(text)
    assert proposal["is_compound"] is True
    assert departments == ["Roads Dept", "Electricity Dept"]
    assert [part["text"] for part in proposal["parts"]] == [
        "road has a pothole", "the streetlight is also not working in ward 3"]
    for part in proposal["parts"]:
        assert text[part["start"]:part["end"]] == part["text"]
        evidence = part["suggestion"]["evidence"][selected_department(part["suggestion"])]
        assert evidence and all(term.casefold() in part["text"].casefold() for term in evidence)
    # Whole-text extraction has a fixed category priority and is unsuitable for
    # describing this parent as only one issue.
    assert intake.process(text)["issue"] == "Electricity"


def test_hinglish_or_complaint_proposes_road_and_streetlight_parts():
    text = "city centre me nescafe ke pass pothole hai or streetlight bhi bnd higai h"
    proposal, departments = _departments(text)
    assert proposal["is_compound"] is True
    assert departments == ["Roads Dept", "Electricity Dept"]
    assert [part["issue"] for part in proposal["parts"]] == ["Road damage", "Electricity"]
    assert [part["text"] for part in proposal["parts"]] == [
        "city centre me nescafe ke pass pothole hai", "streetlight bhi bnd higai h"]
    for part in proposal["parts"]:
        assert text[part["start"]:part["end"]] == part["text"]
        dept = selected_department(part["suggestion"])
        assert part["suggestion"]["evidence"][dept]


def test_new_public_preview_does_not_keep_the_previous_category_or_location(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import register_user

    resident = register_user("fresh-preview@example.test", "Preview Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    root = Path(__file__).resolve().parent.parent
    portal = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    portal.session_state["authenticated_user"] = resident
    portal.run()
    portal.selectbox(key="resident-complaint-state").select_index(1).run()
    portal.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    portal.selectbox(key="resident-complaint-area-mp-gwalior").select_index(1).run()

    portal.text_area[0].set_value("Garbage is not collected near City Centre")
    portal.form_submit_button[0].click().run()
    assert portal.selectbox(key="public-preview-issue").value == "Sanitation"
    portal.selectbox(key="public-preview-issue").set_value("Drainage").run()
    portal.text_input(key="public-preview-location").set_value("Old location").run()

    portal.text_area[0].set_value("The streetlight is not working near City Centre")
    portal.form_submit_button[0].click().run()
    assert not portal.exception
    assert portal.selectbox(key="public-preview-issue").value == "Electricity"
    assert portal.text_input(key="public-preview-location").value == "Morar"


def test_required_multilingual_and_list_split_cases():
    cases = [
        ("Streetlight is not working in Thatipur and garbage is also piled up near the park",
         ["Electricity Dept", "Sanitation Dept"]),
        ("There is a pothole on Ring Road, the drain next to it is blocked, and garbage is not being collected",
         ["Roads Dept", "Drainage Dept", "Sanitation Dept"]),
        ("Subah se water supply nahi aa rahi aur bahar ki sadak bhi tooti hui hai",
         ["Water Supply Dept", "Roads Dept"]),
        ("सड़क पर गड्ढा है और स्ट्रीटलाइट भी बंद है",
         ["Roads Dept", "Electricity Dept"]),
        ("Kachra nahi utha, streetlight band hai",
         ["Sanitation Dept", "Electricity Dept"]),
        ("1. pothole on road\n2. garbage not collected",
         ["Roads Dept", "Sanitation Dept"]),
    ]
    for text, expected in cases:
        proposal, departments = _departments(text)
        assert proposal["is_compound"] is True, text
        assert departments == expected, text
        for part in proposal["parts"]:
            assert text[part["start"]:part["end"]] == part["text"]


def test_same_owner_unclear_and_overlap_inputs_are_not_split():
    single_owner = [
        "The footpath on Station Road is broken and elderly people keep tripping.",
        "There is a pothole and the road is badly broken.",
    ]
    for text in single_owner:
        proposal, _ = _departments(text)
        assert proposal["is_compound"] is False

    overlap_cases = [
        "Waterlogging on the road in front of the bus depot after every rain.",
        "A water pipe burst near Maharaj Bada and water is flooding the road.",
    ]
    for text in overlap_cases:
        proposal, _ = _departments(text)
        assert proposal["is_compound"] is False
        assert proposal["ownership_conflicts"]

    vague, _ = _departments("Problem near my house, please fix.")
    assert vague["is_compound"] is False
    assert vague["parts"] == []


def test_single_word_also_does_not_split_and_vague_context_attaches_to_issue():
    one = propose_split("The streetlight is also not working near the park.")
    assert one["is_compound"] is False
    context = propose_split("The footpath is broken and elderly people keep tripping.")
    assert context["is_compound"] is False


def test_ampersand_and_hinglish_saath_mein_connectors_are_supported_without_splitting_also_alone():
    for text in ("Pothole on the road & garbage is not collected",
                 "Streetlight band hai saath mein kachra nahi utha"):
        proposal, departments = _departments(text)
        assert proposal["is_compound"] is True
        assert len(departments) == 2
    assert propose_split("The streetlight is also not working near the park") ["is_compound"] is False


def test_public_proposal_persists_and_reviewer_confirms_linked_children(db, staff_user, monkeypatch):
    import json
    import pytest
    from core.db import fetch_all, insert_public_submission
    from core.intake import process
    from core.router_baseline import route
    from core.splitter import confirm_split, merge_back

    text = "road has a pothole, and the streetlight is also not working in ward 3"
    proposal = propose_split(text)
    rec = process(text)
    rec.update({"case_id": "CASE-COMPOUND-REPRO", "issue": "multiple issues", "location": "City Centre",
                "state_code": "mp", "state_name": "Madhya Pradesh",
                "city_code": "mp-gwalior", "city_name": "Gwalior",
                "area_code": "mp-gwalior-city-centre", "area_name": "City Centre"})
    parent_id = insert_public_submission(rec, route(text), split_proposal=proposal)
    reviewer = staff_user("reviewer")
    # Geography authorization is covered by the existing role/area suite; this
    # regression focuses on the proposal/child persistence and source spans.
    monkeypatch.setattr("core.splitter.authorize_staff_complaint", lambda *args: None)

    stored = fetch_all("SELECT proposal_json,status FROM split_proposals WHERE parent_id=?", (parent_id,))[0]
    assert stored["status"] == "proposed"
    assert json.loads(stored["proposal_json"])["parts"][0]["text"] == "road has a pothole"
    assert fetch_all("SELECT event_type FROM ledger WHERE complaint_id=? ORDER BY id", (parent_id,))[-1][
        "event_type"] == "split_proposed"
    assert fetch_all("SELECT original_text FROM complaints WHERE id=?", (parent_id,))[0]["original_text"] == text
    assert fetch_all("SELECT id FROM complaints WHERE parent_id=?", (parent_id,)) == []

    child_ids = confirm_split(parent_id, "CASE-COMPOUND-REPRO", proposal["parts"], "reviewer",
                              actor_user=reviewer)
    children = fetch_all("SELECT * FROM complaints WHERE parent_id=? ORDER BY source_start", (parent_id,))
    assert child_ids == [row["id"] for row in children]
    assert [row["issue"] for row in children] == ["Road damage", "Electricity"]
    assert [row["location"] for row in children] == ["City Centre", "City Centre"]
    assert all(text[row["source_start"]:row["source_end"]] == row["original_text"] for row in children)
    assert all(row["case_id"] == "CASE-COMPOUND-REPRO" for row in children)
    assert fetch_all("SELECT status FROM split_proposals WHERE parent_id=?", (parent_id,))[0]["status"] == "confirmed"

    with pytest.raises(ValueError, match="reason"):
        merge_back(parent_id, "reviewer", "", actor_user=reviewer)
    assert merge_back(parent_id, "reviewer", "These items describe one incident.", actor_user=reviewer) == 2
    final_event = fetch_all("SELECT event_type,reason FROM ledger WHERE complaint_id=? ORDER BY id DESC LIMIT 1",
                            (parent_id,))[0]
    assert final_event == {"event_type": "split_merged_back", "reason": "These items describe one incident."}
    assert fetch_all("SELECT original_text FROM complaints WHERE id=?", (parent_id,))[0]["original_text"] == text


def test_citizen_preview_and_staff_review_show_proposal_before_confirmation(db, staff_user, monkeypatch):
    import json
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import register_user
    from core.db import fetch_all
    from core.intake import process
    from core.router_baseline import route
    from core.db import insert_public_submission

    resident = register_user("compound-preview@example.test", "Compound Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    text = "road has a pothole, and the streetlight is also not working in ward 3"
    root = Path(__file__).resolve().parent.parent
    portal = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    portal.session_state["authenticated_user"] = resident
    portal.run()
    portal.selectbox(key="resident-complaint-state").select_index(1).run()
    portal.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    portal.selectbox(key="resident-complaint-area-mp-gwalior").select_index(1).run()
    portal.text_area[0].set_value(text)
    portal.form_submit_button[0].click().run()
    assert not portal.exception
    assert any("Possible separate issues" in item.value for item in portal.subheader)
    assert [portal.selectbox(key=f"public-preview-split-issue-{index}").value for index in range(2)] == [
        "Road damage", "Electricity"]
    assert fetch_all("SELECT id FROM complaints WHERE submitted_by=?", (resident["id"],)) == []
    portal.checkbox(key="public-preview-confirmed").check().run()
    portal.button(key="public-preview-submit").click().run()
    assert not portal.exception
    complaint = fetch_all("SELECT * FROM complaints WHERE submitted_by=?", (resident["id"],))[0]
    assert complaint["original_text"] == text and complaint["issue"] == "multiple issues"
    assert fetch_all("SELECT status FROM split_proposals WHERE parent_id=?", (complaint["id"],))[0]["status"] == "proposed"

    reviewer = staff_user("reviewer")
    db.execute("INSERT INTO government_area_events(user_id,state_code,city_code,area_code,event_type,actor_email,reason) "
               "VALUES(?,?,?,?,?,?,?)", (reviewer["id"], "mp", "mp-gwalior", "*", "approved",
                                            "system:test", "Synthetic test city scope"))
    monkeypatch.setattr("core.geo.is_geography_admin", lambda email: True)
    staff = AppTest.from_file(root / "pages/2_Review.py", default_timeout=30)
    staff.session_state["authenticated_user"] = reviewer
    staff.run()
    assert not staff.exception
    assert any("Proposed sub-issues · not yet confirmed" in item.value for item in staff.subheader)
    assert any("Confirm split" == item.label for item in staff.button)
    parent_id = complaint["id"]
    staff.button(key=f"confirm-split-{parent_id}").click().run()
    assert not staff.exception
    children = fetch_all("SELECT id,case_id,parent_id,source_start,source_end,original_text FROM complaints "
                         "WHERE parent_id=? ORDER BY source_start", (parent_id,))
    assert len(children) == 2
    assert all(child["case_id"] == complaint["case_id"] and child["parent_id"] == parent_id for child in children)
    assert fetch_all("SELECT status FROM split_proposals WHERE parent_id=?", (parent_id,))[0]["status"] == "confirmed"


def test_compound_parent_stays_unassigned_in_staff_case_until_split_confirmation(db, staff_user):
    import json
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from core.auth import register_user
    from core.db import fetch_all
    from core.geo import configure_staff_location
    from core.staff_dashboard import get_staff_case_detail

    resident = register_user("compound-parent@example.test", "Compound Resident", "Secure!River72",
                             password_confirm="Secure!River72")
    text = "ROAD HAS A POTHOLE AND ALSO THE STREETLIGHT IS NOT WORKING IN WARD 4"
    root = Path(__file__).resolve().parent.parent
    portal = AppTest.from_file(root / "pages/10_Public_Portal.py", default_timeout=30)
    portal.session_state["authenticated_user"] = resident
    portal.run()
    portal.selectbox(key="resident-complaint-state").select_index(1).run()
    portal.selectbox(key="resident-complaint-city-mp").select_index(1).run()
    portal.selectbox(key="resident-complaint-area-mp-gwalior").select_index(1).run()
    portal.text_area[0].set_value(text)
    portal.form_submit_button[0].click().run()
    assert not portal.exception
    assert portal.session_state["public_intake_preview"]["route"]["abstained"] is False
    assert any("complete complaint stays unassigned" in item.value for item in portal.info)

    portal.checkbox(key="public-preview-confirmed").check().run()
    portal.button(key="public-preview-submit").click().run()
    complaint = fetch_all("SELECT * FROM complaints WHERE submitted_by=?", (resident["id"],))[0]
    saved_route = fetch_all("SELECT top3,abstained FROM suggestions WHERE complaint_id=?",
                            (complaint["id"],))[0]
    assert saved_route["abstained"] == 1
    assert {item["dept"] for item in json.loads(saved_route["top3"])} == {"Roads Dept", "Electricity Dept"}

    reviewer = staff_user("reviewer")
    configure_staff_location(reviewer, complaint["state_code"], complaint["city_code"], complaint["area_code"])
    detail = get_staff_case_detail(reviewer, complaint["id"])
    assert detail["suggested_department"] is None
    assert detail["current_department"] is None
    assert detail["uncertain"] is True
    assert detail["split_status"] == "proposed"


def test_voice_ready_message_uses_streamlit_component_protocol():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "components/speech_input/index.html").read_text(encoding="utf-8")
    assert "type:'streamlit:componentReady',apiVersion:1" in source
    assert "send('streamlit:componentReady'" not in source


def test_split_proposal_schema_adds_to_legacy_copy_idempotently(monkeypatch, tmp_path):
    import sqlite3
    from core import db as db_module

    legacy = tmp_path / "legacy-copy.sqlite3"
    conn = sqlite3.connect(legacy)
    try:
        conn.execute("CREATE TABLE complaints (id INTEGER PRIMARY KEY,case_id TEXT NOT NULL,parent_id INTEGER,"
                     "original_text TEXT NOT NULL,language TEXT NOT NULL,normalized_text TEXT,channel TEXT,"
                     "location TEXT,issue TEXT,requested_action TEXT,missing_fields TEXT,is_synthetic INTEGER NOT NULL DEFAULT 1,"
                     "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
        conn.execute("INSERT INTO complaints(case_id,original_text,language,location) VALUES(?,?,?,?)",
                     ("CASE-LEGACY-SPLIT", "road has a pothole", "english", "Gole Ka Mandir"))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db_module, "DB_PATH", legacy)

    db_module.init_db()
    conn = db_module.get_conn()
    try:
        row = conn.execute("SELECT original_text,location,source_start,source_end FROM complaints").fetchone()
        assert tuple(row) == ("road has a pothole", "Gole Ka Mandir", None, None)
        assert {item["name"] for item in conn.execute("PRAGMA table_info(split_proposals)")} == {
            "parent_id", "proposal_json", "status", "lead_department", "created_at", "updated_at",
            "decided_by", "reason"}
    finally:
        conn.close()
    db_module.init_db()
    assert db_module.fetch_all("SELECT original_text FROM complaints WHERE case_id='CASE-LEGACY-SPLIT'") == [
        {"original_text": "road has a pothole"}]


def test_splitter_eval_is_synthetic_family_grouped_and_has_required_sample_counts():
    import json
    from pathlib import Path

    dataset = json.loads((Path(__file__).resolve().parent.parent / "data/splitter_eval.json")
                         .read_text(encoding="utf-8"))
    rows = dataset["rows"]
    positives = [row for row in rows if row["expected_compound"]]
    negatives = [row for row in rows if not row["expected_compound"]]
    assert len(positives) >= 30 and len(negatives) >= 20
    assert all(row["tag"] == "SYNTHETIC" and row["labeller"] == "AI_AUTHORED_SYNTHETIC" for row in rows)
    assert all(len({row["split"] for row in rows if row["family"] == family}) == 1
               for family in {row["family"] for row in rows})
    assert {row["language"] for row in rows} == {"english", "hindi", "hinglish"}
