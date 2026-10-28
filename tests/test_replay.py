"""The offline replay (``src.activities.replay``) and the rebuild built on it (``src.activities.rebuild``): a recorded session's captures recomputed, compared, drawn, and swapped in with a backup."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from PIL import Image

from src.activities import rebuild, replay
from src.activities.registry import counted_for, parser
from src.errors import DomainError
from src.jsonl import read_jsonl
from src.sessions.model import dump_session, parse_session
from tests.fixtures.demo import build_demo_session, write_demo_run

VENDORED_FONT = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static" / "fonts" / "PatrickHand-Regular.ttf"


@pytest.fixture
def recorded(isolated_env: Path) -> Path:
    _, folder = build_demo_session(isolated_env / "sessions" / "demo" / "replay")
    write_demo_run(folder, pngs=False)
    return folder


def _digest(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_replay_without_overrides_equals_every_frozen_result_and_leaves_the_folder_untouched(
        recorded: Path, tmp_path: Path) -> None:
    before = _digest(recorded)
    out = tmp_path / "out"
    assert replay.main([str(recorded), "--out", str(out)]) == 0
    assert _digest(recorded) == before
    frozen = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (recorded / "live" / "captures").glob("*.json")}
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))["items"]
    assert sorted(c["item_id"] for c in summary) == sorted(frozen)
    for c in summary:
        assert c["same_result"], c["item_id"]
        replayed = json.loads((out / f"{c['item_id']}.json").read_text(encoding="utf-8"))
        assert replayed["result"] == frozen[c["item_id"]]["result"]
        assert replayed["answers"] == frozen[c["item_id"]]["answers"]
    cloud = next(c for c in summary if c["item_id"] == "act-kryptonite")
    assert cloud["whole_before"] == cloud["whole_after"] and cloud["added"] == cloud["removed"] == [] and not cloud["merged"]


def test_replay_never_writes_inside_the_session_folder(recorded: Path) -> None:
    with pytest.raises(ValueError):
        replay.replay(recorded, recorded / "replay")


def test_a_set_override_changes_only_the_named_option(recorded: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = replay.replay(recorded, out, items=["act-kryptonite", "act-weather"], sets={"terms": "words"})
    frozen_item = json.loads((recorded / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))["item"]
    item = json.loads((out / "act-kryptonite.json").read_text(encoding="utf-8"))["item"]
    assert item["options"] == {**(frozen_item["options"] or {}), "terms": "words"}
    weather = json.loads((out / "act-weather.json").read_text(encoding="utf-8"))["item"]
    assert "terms" not in (weather["options"] or {})  # a scale has no such option
    cloud = next(c for c in summary if c["item_id"] == "act-kryptonite")
    assert not cloud["same_result"]
    assert "meetings without agenda" in cloud["removed"] and "agenda" in cloud["added"]
    assert cloud["whole_after"] < cloud["whole_before"]
    assert "meetings without agenda" in [a["text"] for a in cloud["not_whole"]]


def test_compare_reports_an_entry_merged_into_another() -> None:
    mod = parser("word_cloud")
    opts = {"terms": "auto", "merge_variants": True}

    def record(texts: list[str], merge: bool) -> dict:
        msgs = [{"id": i, "sender": f"P{i}", "text": t} for i, t in enumerate(texts, 1)]
        parsed = [mod.parse(m, opts) for m in msgs]
        result = mod.aggregate([p for p in parsed if p], {**opts, "merge_variants": merge})
        return {"item": {"id": "act-x", "type": "word_cloud"}, "result": result,
                "answers": [dict(m, hidden=False, parsed=p) for m, p in zip(msgs, parsed, strict=True)]}

    texts = ["Calma", "calma", "Humor"]
    cmp = replay.compare(record(texts, merge=False), record(texts, merge=True))
    assert cmp["merged"] == {"Calma": "calma"} and cmp["count_changes"] == {"calma": [1, 2]}
    assert cmp["whole_before"] == cmp["whole_after"] == 3


@pytest.mark.parametrize(("text", "whole"), [
    ("perfeccionismo", True), ("miedo a fallar", True), ("¡Sí!", True), ("jaja reuniones", True),
    ("reuniones sin agenda y sin fin", False),
])
def test_word_cloud_says_whether_an_answer_is_shown_whole(text: str, whole: bool) -> None:
    mod = parser("word_cloud")
    opts = {"terms": "auto", "merge_variants": True, "stopwords": "es"}
    parsed = mod.parse({"text": text, "sender": "P"}, opts)
    result = mod.aggregate([parsed], opts)
    c = counted_for("word_cloud", text, parsed, result)
    assert c is not None and c["whole"] is whole and c["entries"]


def test_render_draws_each_replayed_cloud_through_the_real_stage(recorded: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert replay.main([str(recorded), "--out", str(out), "--item", "act-kryptonite", "--render"]) == 0
    with Image.open(out / "act-kryptonite.png") as im:
        assert im.size == (1920, 1080)
    assert not list(out.glob("render-*"))  # the temp copy of the session is gone


def _events(*rows: tuple[str, dict]) -> list[dict]:
    """Events at whole seconds past a fixed instant, as ``events.jsonl`` holds them."""
    return [{"at": f"2026-10-27T18:00:{sec:02d}+00:00", "event": name, **fields} for sec, (name, fields) in enumerate(rows)]


def test_a_window_is_cut_where_the_activity_was_left_the_timer_hid_it_or_the_session_closed() -> None:
    t0 = int(datetime.fromisoformat("2026-10-27T18:00:00+00:00").timestamp() * 1000)
    events = _events(("capture_start", {"item_id": "a"}), ("item", {"item_id": "a"}), ("timer_end", {"item_id": "a", "end": "hide"}),
                     ("item", {"item_id": "b"}), ("session_closed", {}))
    # opened at second 0: the move to "a" itself and nothing else before the hide at second 2
    assert replay.cut_windows("a", [[t0 - 1, None]], events) == [[t0 - 1, t0 + 2000]]
    assert replay.cut_windows("a", [[t0 + 2500, None]], events) == [[t0 + 2500, t0 + 3000]]  # then the move to "b"
    assert replay.cut_windows("a", [[t0 + 3500, None]], events) == [[t0 + 3500, t0 + 4000]]  # then the close
    assert replay.cut_windows("a", [[t0 - 1, t0 + 500]], events) == [[t0 - 1, t0 + 500]]  # stopped sooner: unchanged
    assert replay.cut_windows("a", [[t0 + 5000, None]], events) == [[t0 + 5000, None]]  # nothing after: stays open


def test_replay_leaves_out_excluded_senders_and_cuts_windows_on_request(recorded: Path, tmp_path: Path) -> None:
    sender = json.loads((recorded / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))["answers"][0]["sender"]
    out = tmp_path / "out"
    base = replay.replay(recorded, out, items=["act-kryptonite"])[0]
    cmp = replay.replay(recorded, out, items=["act-kryptonite"], exclude=(sender,))[0]
    assert base["excluded_rows"] == 0 and cmp["excluded_rows"] >= 1
    assert cmp["answers"] == base["answers"] - cmp["excluded_rows"] and not cmp["same_result"]
    totals = json.loads((out / "summary.json").read_text(encoding="utf-8"))["totals"]
    assert totals["excluded_rows"] == cmp["excluded_rows"]
    replayed = json.loads((out / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert [a["sender"] for a in replayed["answers"] if a.get("excluded")] == [sender] * cmp["excluded_rows"]


def test_replay_cuts_a_capture_that_was_never_stopped_where_the_activity_was_left(recorded: Path, tmp_path: Path) -> None:
    live = recorded / "live"
    frozen = json.loads((live / "captures" / "act-enemy.json").read_text(encoding="utf-8"))
    windows = [[w[0], None] for w in frozen["windows"]]  # opened and never stopped
    (live / "captures.json").write_text(json.dumps({"windows": {"act-enemy": windows}, "hidden": [], "places": {}}), encoding="utf-8")
    (live / "captures" / "act-enemy.json").unlink()  # so never frozen either
    left = datetime.fromtimestamp(windows[-1][0] / 1000 + 1).astimezone().isoformat()
    with (live / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": left, "event": "item", "item_id": "somewhere-else"}) + "\n")
    kept = replay.replay(recorded, tmp_path / "kept", items=["act-enemy"], stop_on_leave=False)[0]
    cut = replay.replay(recorded, tmp_path / "cut", items=["act-enemy"], stop_on_leave=True)[0]
    assert (kept["windows_cut"], cut["windows_cut"]) == (0, 1)
    assert cut["frozen"] is False and cut["answers"] <= kept["answers"]


def _row(id_: int, text: str, time: str, received: str, sender: str = "Ana") -> dict:
    """A chat row typed at Zoom's ``time`` and received at the local clock ``received`` (HH:MM) that day."""
    ms = int(datetime.fromisoformat(f"2026-10-27T{received}:30").timestamp() * 1000)
    return {"id": id_, "sender": sender, "text": text, "time": time, "received_at": ms}


def test_a_row_received_over_a_minute_after_its_zoom_time_with_an_identical_earlier_row_is_a_repost() -> None:
    chat = [
        _row(1, "calm", "18:20", "18:20"),
        _row(2, "calm", "18:20", "19:11"),  # the window came back: the same row posted again, 51 minutes late
        _row(3, "calm", "18:20", "18:21"),  # an identical row a minute late is a slow read at most
        _row(4, "late", "18:21", "19:11"),  # late, but nothing earlier says it: the only record of that message
        _row(5, "calm", "18:30", "18:30", sender="Ben"),  # same text, another sender
        _row(6, "yes", "23:59", "23:59"),
        _row(7, "yes", "23:59", "00:02"),  # past midnight: three minutes late
        {**_row(8, "ok", "", "19:11"), "time": ""},  # a row without Zoom's time cannot be told
        {**_row(9, "ok", "", "19:12"), "time": ""},
    ]
    assert replay.reposted_ids(chat) == {2, 7}


def test_a_repost_is_a_twin_when_its_earlier_copy_is_stored_under_a_renamed_sender() -> None:
    chat = [
        _row(1, "Calm", "18:20", "18:20", sender="Ana (old name)"),
        _row(2, "Éxito", "18:21", "18:21", sender="Iphone de Ben"),
        _row(3, "calm", "18:20", "19:11", sender="Ana"),  # renamed in Zoom: the window's history relabels the row
        _row(4, "exito", "18:21", "19:11", sender="Ben"),  # merged since: case and accents are folded
        _row(5, "calm", "18:20", "19:11", sender="Cleo"),  # third copy of a row already dropped once: still a twin
        _row(6, "brand new", "18:25", "19:11", sender="Ana"),  # late, but no earlier text and time: kept
        _row(7, "calm", "18:40", "19:11", sender="Ana"),  # same text, another minute: kept
    ]
    assert replay.reposted_ids(chat) == {3, 4, 5}


def test_replay_drops_reposted_rows_only_when_asked(recorded: Path, tmp_path: Path) -> None:
    live = recorded / "live"
    chat = [json.loads(line) for line in (live / "chat.jsonl").read_text(encoding="utf-8").splitlines()]
    start = json.loads((live / "captures" / "act-enemy.json").read_text(encoding="utf-8"))["windows"][0][0]
    first = json.loads((live / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))["answers"][0]
    original = next(m for m in chat if m["id"] == first["id"])
    with (live / "chat.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**original, "id": len(chat) + 1, "received_at": start + 1_000}) + "\n")
    kept = replay.replay(recorded, tmp_path / "kept", items=["act-enemy"])[0]
    dropped = replay.replay(recorded, tmp_path / "dropped", items=["act-enemy"], drop_reposts=True)[0]
    assert (kept["reposts_dropped"], dropped["reposts_dropped"]) == (0, 1)
    assert dropped["answers"] == kept["answers"] - 1
    assert json.loads((tmp_path / "dropped" / "summary.json").read_text(encoding="utf-8"))["totals"]["reposts_dropped"] == 1


def _with_font(folder: Path, font: Path) -> None:
    raw = yaml.safe_load((folder / "session.yaml").read_text(encoding="utf-8"))
    raw["font"] = {"file": str(font)}
    (folder / "session.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


def test_the_picture_waits_for_the_session_font_and_is_not_written_when_it_does_not_load(recorded: Path, tmp_path: Path) -> None:
    good = Path(shutil.copy(VENDORED_FONT, tmp_path / "Synthetic-Regular.ttf"))
    _with_font(recorded, good)
    out = tmp_path / "good"
    assert replay.main([str(recorded), "--out", str(out), "--item", "act-kryptonite", "--render"]) == 0
    assert (out / "act-kryptonite.png").is_file()

    broken = tmp_path / "Broken.ttf"
    broken.write_bytes(b"not a font")  # served as a font, never decodes: the title would show the fallback
    _with_font(recorded, broken)
    out = tmp_path / "broken"
    assert replay.main([str(recorded), "--out", str(out), "--item", "act-kryptonite", "--render"]) == 2
    assert not (out / "act-kryptonite.png").exists()


# ---- Rebuild results (src.activities.rebuild) ----

REPOSTED = 2  # rows posted again into the last capture


def _captures_digest(folder: Path) -> dict[str, str]:
    live = folder / "live"
    return {k: v for k, v in _digest(live).items() if k.startswith("captures") or k == "captures.json"}


@pytest.fixture
def old_session(isolated_env: Path) -> tuple[str, Path]:
    """A run whose chat holds rows the reader posted again, frozen the way a session before the fix froze them."""
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "rebuild", isolated_env / "sessions.local.yaml")
    write_demo_run(folder, pngs=True)
    live = folder / "live"
    chat = read_jsonl(live / "chat.jsonl")
    first = json.loads((live / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    last_start = json.loads((live / "captures" / "act-enemy.json").read_text(encoding="utf-8"))["windows"][0][0]
    originals = [m for m in chat if m["id"] in {a["id"] for a in first["answers"] if not a["hidden"]}][:REPOSTED]
    with (live / "chat.jsonl").open("a", encoding="utf-8") as fh:
        for i, m in enumerate(originals, start=1):
            fh.write(json.dumps({**m, "id": len(chat) + i, "received_at": last_start + 1_000 * i}) + "\n")
    assert len(replay.reposted_ids(read_jsonl(live / "chat.jsonl"))) == REPOSTED
    # frozen without the cut, as before: the reposted rows are counted in the last capture
    old = next(r for r, c in replay.replay_records(folder, items=["act-enemy"]))
    (live / "captures" / "act-enemy.json").write_text(rebuild._record(old), encoding="utf-8")
    windows = {p.stem: json.loads(p.read_text(encoding="utf-8"))["windows"] for p in (live / "captures").glob("*.json")}
    (live / "captures.json").write_text(json.dumps({"windows": windows, "hidden": [], "places": {}}), encoding="utf-8")
    return sid, folder


def _enemy(folder: Path) -> dict:
    return json.loads((folder / "live" / "captures" / "act-enemy.json").read_text(encoding="utf-8"))


def test_preview_shows_before_and_after_per_activity_and_writes_nothing(old_session: tuple[str, Path]) -> None:
    _, folder = old_session
    before = _digest(folder)
    p = rebuild.preview(folder)
    assert _digest(folder) == before
    enemy = next(r for r in p["activities"] if r["item_id"] == "act-enemy")
    assert (enemy["answers_before"], enemy["answers_after"], enemy["reposts_dropped"]) == (4 + REPOSTED, 4, REPOSTED)
    assert p["changed"] == 1 and p["reposts_dropped"] == REPOSTED and p["backup"] is None
    assert all(r["same_result"] for r in p["activities"] if r["item_id"] != "act-enemy")


def test_a_rebuild_uses_the_options_the_plan_says_now(old_session: tuple[str, Path]) -> None:
    _, folder = old_session
    session = parse_session(yaml.safe_load((folder / "session.yaml").read_text(encoding="utf-8")))
    cloud = next(it for sec in session.sections for it in sec.items if it.id == "act-kryptonite")
    cloud.options = {**(cloud.options or {}), "terms": "words"}
    (folder / "session.yaml").write_text(yaml.safe_dump(dump_session(session), sort_keys=False, allow_unicode=True), encoding="utf-8")
    row = next(r for r in rebuild.preview(folder)["activities"] if r["item_id"] == "act-kryptonite")
    assert not row["same_result"] and "agenda" in row["added"] and "meetings without agenda" in row["removed"]


def test_apply_backs_up_rebuilds_records_and_pictures_and_undo_restores_every_byte(client, old_session: tuple[str, Path]) -> None:
    sid, folder = old_session
    before = _captures_digest(folder)
    r = client.post(f"/api/sessions/{sid}/rebuild")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backup"].startswith(rebuild.BACKUP_PREFIX) and body["reposts_dropped"] == REPOSTED
    assert _digest(folder / "live" / body["backup"]) == before  # captures/ and captures.json, as they were
    rec = _enemy(folder)
    assert len(rec["answers"]) == 4 and rec["frozen_at"] and set(rec) == set(rebuild.RECORD_KEYS)
    after = _captures_digest(folder)
    assert after["captures/act-enemy.png"] != before["captures/act-enemy.png"]  # drawn again through the stage
    assert client.get(f"/api/sessions/{sid}/results").json()["rebuild_backup"] == body["backup"]

    assert client.post(f"/api/sessions/{sid}/rebuild/undo").status_code == 200
    assert _captures_digest(folder) == before
    assert rebuild.backups(folder) == [] and client.get(f"/api/sessions/{sid}/results").json()["rebuild_backup"] is None
    assert client.post(f"/api/sessions/{sid}/rebuild/undo").json()["error"]["code"] == "no_backup"


def test_a_picture_that_cannot_be_drawn_changes_nothing(old_session: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _, folder = old_session
    before = _digest(folder)
    monkeypatch.setattr(rebuild, "render_all", lambda *_: ["act-enemy"])
    with pytest.raises(DomainError) as exc:
        rebuild.apply(folder)
    assert exc.value.code == "render_failed"
    assert _digest(folder) == before  # no backup folder either


def test_a_live_session_and_a_session_without_frozen_captures_cannot_be_rebuilt(client, old_session: tuple[str, Path],
                                                                                 isolated_env: Path) -> None:
    sid, _ = old_session
    client.app.state.live.session_id = sid
    r = client.get(f"/api/sessions/{sid}/rebuild")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "session_live")
    client.app.state.live.session_id = None
    _, empty = build_demo_session(isolated_env / "sessions" / "demo" / "empty", isolated_env / "sessions.local.yaml")
    with pytest.raises(DomainError) as exc:
        rebuild.apply(empty)
    assert exc.value.code == "nothing_to_rebuild"
