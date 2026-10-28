"""Rebuild results: a closed session's frozen captures recomputed with today's rules (``src.activities.rebuild``)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.activities import rebuild, replay
from src.errors import DomainError
from src.jsonl import read_jsonl
from src.sessions.model import dump_session, parse_session
from tests.fixtures.demo import build_demo_session, write_demo_run

REPOSTED = 2  # rows posted again into the last capture


def _digest(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


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
