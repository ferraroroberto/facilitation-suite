"""Session model, store, ledger and the sessions API."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import load_config
from src.sessions.model import ITEM_ID, Session, dump_session, new_id, parse_session
from src.sessions.offline import check_folder
from src.sessions.store import SessionError, SessionStore

PLAN = {
    "schema": 1,
    "title": "Ice break · cohort",
    "date": "2026-10-27T18:00:00+01:00",
    "duration_minutes": 120,
    "future_top_level": {"kept": True},
    "sections": [
        {
            "name": "Personal readme",
            "minutes": 30,
            "colour": "teal",
            "items": [
                {"kind": "slide", "slide_id": 263, "title": "Washing instructions", "profile": "camera_strip",
                 "annotation": "hand-written key"},
                {"kind": "activity", "id": "a-kryptonite", "type": "word_cloud",
                 "question": "What switches this group off?", "font": {"family": "Patrick Hand", "size_px": 72},
                 "timer": {"enabled": True, "seconds": 180, "start": "with_capture", "show_on": "stage", "end": "stop_capture"},
                 "options": {"merge_variants": True, "stopwords": "es"}},
            ],
        }
    ],
}


def test_unknown_keys_survive_a_round_trip() -> None:
    session = parse_session(PLAN)
    out = dump_session(session)
    assert out["future_top_level"] == {"kept": True}
    assert out["sections"][0]["colour"] == "teal"
    assert out["sections"][0]["items"][0]["annotation"] == "hand-written key"
    again = dump_session(parse_session(out))
    assert again == out


def test_ids_are_stable_and_unique() -> None:
    session = parse_session(PLAN)
    items = session.all_items()
    assert items[0].id == "slide-263"
    assert items[1].id == "a-kryptonite"
    assert session.sections[0].id == "sec-personal-readme"


def test_slide_items_dump_without_noise() -> None:
    out = dump_session(parse_session(PLAN))
    slide = out["sections"][0]["items"][0]
    assert "question" not in slide and "options" not in slide and "timer" not in slide


def test_new_items_have_no_timer() -> None:
    session = parse_session({"sections": [{"name": "A", "items": [{"kind": "slide", "slide_id": 1}]}]})
    assert session.all_items()[0].timer is None


def test_unknown_activity_type_is_rejected() -> None:
    bad = {"sections": [{"name": "A", "items": [{"kind": "activity", "type": "karaoke"}]}]}
    with pytest.raises(ValueError, match="unknown activity type"):
        parse_session(bad)


def test_newer_schema_is_refused() -> None:
    with pytest.raises(ValueError, match="newer"):
        parse_session({"schema": 99})


def _store(tmp: Path) -> SessionStore:
    return SessionStore(load_config(), ledger=tmp / "ledger.yaml")


def test_create_lists_and_loads(tmp_path: Path) -> None:
    st = _store(tmp_path)
    entry = st.create("Ice break · cohort A", "icebreak", "cohort-a", root=str(tmp_path / "root"))
    folder = Path(entry.path)
    assert folder == tmp_path / "root" / "icebreak" / "cohort-a"
    for sub in ("slides", "live", "exports"):
        assert (folder / sub).is_dir()
    assert [e.id for e in st.entries()] == [entry.id]
    assert st.load(entry.id).title == "Ice break · cohort A"
    ledger = yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))
    assert ledger["sessions"][0]["path"] == str(folder)


def test_create_refuses_an_existing_session(tmp_path: Path) -> None:
    st = _store(tmp_path)
    st.create("A", "w", "s", root=str(tmp_path))
    with pytest.raises(SessionError) as exc:
        st.create("A again", "w", "s", root=str(tmp_path))
    assert exc.value.code == "session_exists"


def test_duplicate_copies_the_plan_not_live_data(tmp_path: Path) -> None:
    st = _store(tmp_path)
    entry = st.create("Cohort A", "icebreak", "cohort-a", root=str(tmp_path))
    folder = Path(entry.path)
    (folder / "slides" / "001.png").write_bytes(b"png")
    (folder / "live" / "chat.jsonl").write_text("{}\n", encoding="utf-8")
    session = st.load(entry.id)
    session.sections = parse_session(PLAN).sections
    st.save(entry.id, session)

    dup = st.duplicate(entry.id, "Cohort B", "cohort-b")
    dfolder = Path(dup.path)
    assert dfolder == folder.parent / "cohort-b"
    assert (dfolder / "slides" / "001.png").is_file()
    assert not (dfolder / "live" / "chat.jsonl").exists()
    copy = st.load(dup.id)
    assert copy.title == "Cohort B" and copy.date is None
    assert len(copy.all_items()) == 2


def test_add_existing_and_remove_keeps_the_folder(tmp_path: Path) -> None:
    st = _store(tmp_path)
    entry = st.create("A", "w", "s", root=str(tmp_path))
    st.remove(entry.id)
    assert st.entries() == []
    assert (Path(entry.path) / "session.yaml").is_file()
    again = st.add_existing(entry.path)
    assert again.id == entry.id
    with pytest.raises(SessionError):
        st.add_existing(str(tmp_path / "nowhere"))


def test_offline_check_on_a_local_folder(tmp_path: Path) -> None:
    folder = tmp_path / "session"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("x", encoding="utf-8")
    (folder / "sub" / "b.txt").write_text("y", encoding="utf-8")
    report = check_folder(folder)
    assert report.state == "ok"
    assert report.total == 2 and report.local == 2 and report.cloud_only == []


def test_offline_check_missing_folder_is_unknown(tmp_path: Path) -> None:
    assert check_folder(tmp_path / "nope").state == "unknown"


# -- API ------------------------------------------------------------------------


def test_api_create_get_put(client, tmp_path: Path) -> None:
    res = client.post("/api/sessions", json={"title": "Cohort A", "workshop": "icebreak", "folder": "cohort-a",
                                             "date": "2026-10-27T18:00:00", "duration_minutes": 120})
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    listing = client.get("/api/sessions").json()["sessions"]
    assert [s["id"] for s in listing] == [sid]

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["title"] == "Cohort A"
    keys = [c["key"] for c in detail["readiness"]]
    assert keys == ["slides", "activities", "roster", "groups", "offline", "reader", "obs", "zoom_update"]
    assert {c["state"] for c in detail["readiness"]} <= {"ok", "warn", "todo", "unknown"}

    plan = dict(PLAN, title="Cohort A (edited)")
    res = client.put(f"/api/sessions/{sid}", json={"session": plan})
    assert res.status_code == 200, res.text
    again = client.get(f"/api/sessions/{sid}").json()
    assert again["session"]["title"] == "Cohort A (edited)"
    assert again["session"]["future_top_level"] == {"kept": True}
    assert client.get("/api/sessions").json()["sessions"][0]["name"] == "Cohort A (edited)"


def test_api_put_invalid_plan_is_422(client) -> None:
    sid = client.post("/api/sessions", json={"title": "X"}).json()["id"]
    res = client.put(f"/api/sessions/{sid}", json={"session": {"sections": [{"items": [{"kind": "nope"}]}]}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_session"


def test_api_unknown_session_is_404(client) -> None:
    res = client.get("/api/sessions/deadbeef00")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "session_not_found"


def test_api_missing_folder_shows_in_the_list(client, tmp_path: Path) -> None:
    import shutil

    created = client.post("/api/sessions", json={"title": "Gone", "workshop": "w", "folder": "gone"}).json()
    shutil.rmtree(created["path"])
    row = client.get("/api/sessions").json()["sessions"][0]
    assert row["status"] == "missing"


def test_session_model_default_is_valid() -> None:
    assert dump_session(Session())["schema"] == 1


def test_activity_types_come_from_the_plugin_folders(client) -> None:
    types = client.get("/api/activities").json()["types"]
    assert [t["type"] for t in types] == ["word_cloud", "map", "scale", "cards", "feed", "groups_reveal",
                                           "quiz_lobby", "quiz", "quiz_podium"]
    wc = types[0]
    assert {o["key"] for o in wc["options"]} >= {"merge_variants", "stopwords"}
    assert types[-1]["capture"] is False


def _activities_check(tmp_path: Path, items: list[dict]) -> dict:
    from src.sessions import readiness
    from src.sessions.offline import OfflineReport

    session = parse_session({"schema": 1, "sections": [{"name": "S", "items": items}]})
    return next(c for c in readiness.build(tmp_path, session, OfflineReport()) if c["key"] == "activities")


def test_readiness_skips_types_without_a_question_but_still_warns_on_empty_ones(tmp_path: Path) -> None:
    quiz = {"kind": "activity", "type": "quiz", "question": "2 + 2?", "options": {"answer_1": "4", "correct": "1"}}
    frame = [{"kind": "activity", "type": "quiz_lobby"}, quiz, {"kind": "activity", "type": "quiz_podium"},
             {"kind": "activity", "type": "groups_reveal"}]
    assert _activities_check(tmp_path, frame)["state"] == "ok"  # #85: lobby and podium have no question by design

    for empty in ({**quiz, "question": " "}, {"kind": "activity", "type": "word_cloud"}):
        got = _activities_check(tmp_path, [*frame, empty])
        assert (got["state"], got["detail"]) == ("warn", "1 without a question")


def test_demo_fixture_is_a_valid_session(tmp_path: Path) -> None:
    from tests.fixtures.demo import build_demo_session

    sid, folder = build_demo_session(tmp_path / "demo")
    st = SessionStore(load_config(), ledger=tmp_path / "l.yaml")
    st.add_existing(str(folder))
    session = st.load(sid)
    assert len(session.sections) == 5
    assert sum(1 for it in session.all_items() if it.kind == "activity") == 7


# ---- the item-id rule (ITEM_ID): one pattern for the model and every route ----

@pytest.mark.parametrize("item_id", [
    "slide-263", "slide-2147483647",                        # slide-<PowerPoint SlideID>
    new_id("act"), new_id("brk"), new_id("bko"), new_id("slide"), new_id("item"),
    new_id("sli"), new_id("bre"),                           # ensure_ids' collision fallback (kind[:3])
    "a-kryptonite", "brk", "act-Q1", "act_q1", "x" * 80,    # written by hand
])
def test_every_id_the_app_makes_or_accepts_fits_and_is_kept(item_id: str) -> None:
    assert ITEM_ID.fullmatch(item_id)
    session = parse_session({"sections": [{"items": [{"kind": "activity", "id": item_id}]}]})
    assert session.all_items()[0].id == item_id


def test_an_empty_or_duplicate_id_is_still_assigned() -> None:
    session = parse_session({"sections": [{"items": [
        {"kind": "slide", "slide_id": 300}, {"kind": "activity"}, {"kind": "activity", "id": "act-a"},
        {"kind": "activity", "id": "act-a"}]}]})
    ids = [it.id for it in session.all_items()]
    assert ids[0] == "slide-300" and ids[2] == "act-a" and len(set(ids)) == 4
    assert all(ITEM_ID.fullmatch(i) for i in ids)


@pytest.mark.parametrize(("item_id", "fixed"), [
    ("act Q1", "act-Q1"), ("act.q1", "act-q1"), ("../x", "x"), ("café", "cafe"), ("y" * 100, "y" * 80),
])
def test_an_invalid_hand_edited_id_is_made_to_fit_the_same_way_every_load(item_id: str, fixed: str, caplog) -> None:
    raw = {"sections": [{"items": [{"kind": "activity", "id": item_id}]}]}
    with caplog.at_level("WARNING", logger="src.sessions.model"):
        assert parse_session(raw).all_items()[0].id == fixed
    assert parse_session(raw).all_items()[0].id == fixed
    assert "is not valid" in caplog.text


@pytest.mark.parametrize(("item_id", "fixed"), [
    ("con", "con-x"), ("CON", "CON-x"), ("Nul", "Nul-x"), ("prn", "prn-x"), ("aux", "aux-x"),
    ("com1", "com1-x"), ("COM9", "COM9-x"), ("lpt1", "lpt1-x"), ("LPT9", "LPT9-x"),
])
def test_a_windows_reserved_device_name_is_repaired_on_load(item_id: str, fixed: str, caplog) -> None:
    # con/prn/aux/nul/com1-9/lpt1-9 name real Windows devices: `live/captures/con.json` cannot be
    # created on Windows no matter the case, so ensure_ids has to repair it like any other bad id.
    raw = {"sections": [{"items": [{"kind": "activity", "id": item_id}]}]}
    with caplog.at_level("WARNING", logger="src.sessions.model"):
        assert parse_session(raw).all_items()[0].id == fixed
    assert ITEM_ID.fullmatch(fixed)
    assert "is not valid" in caplog.text


def test_a_windows_reserved_name_is_only_flagged_as_the_whole_id() -> None:
    # "conf"/"act-con" merely contain "con" — only the bare device name is reserved.
    session = parse_session({"sections": [{"items": [
        {"kind": "activity", "id": "conf"}, {"kind": "activity", "id": "act-con"}]}]})
    ids = [it.id for it in session.all_items()]
    assert ids == ["conf", "act-con"]


def test_an_id_with_nothing_valid_left_gets_a_new_one_and_the_session_still_opens() -> None:
    session = parse_session({"sections": [{"items": [
        {"kind": "activity", "id": "!!!"}, {"kind": "activity", "id": "act Q1"}, {"kind": "activity", "id": "act-Q1"}]}]})
    ids = [it.id for it in session.all_items()]
    assert ids[0].startswith("act-") and ids[1] == "act-Q1" and ids[2] != "act-Q1"
    assert len(set(ids)) == 3 and all(ITEM_ID.fullmatch(i) for i in ids)


def test_both_capture_routes_use_the_one_rule(client) -> None:
    # A hand-written id the Results tab accepted used to 404 on the live route (the stage's freeze).
    r = client.get("/api/live/captures/act-Q1")
    assert r.status_code == 404 and r.json()["error"]["message"] == "Not captured yet"
    r = client.get("/api/live/captures/act.Q1")
    assert r.status_code == 404 and r.json()["error"]["message"] == "No such capture"
