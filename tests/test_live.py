"""The live hub: run order, movement, timers, clocks, durability and /ws sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import load_config
from src.live.actions import run_action
from src.live.hub import LiveError, LiveHub, now_ms
from src.live.plan import build_run, one_line
from src.sessions.model import parse_session
from src.sessions.store import SessionStore
from tests.fixtures.demo import PLAN, build_demo_session


@pytest.fixture
def demo(isolated_env: Path) -> tuple[str, Path]:
    return build_demo_session(isolated_env / "sessions" / "demo" / "live", isolated_env / "sessions.local.yaml")


@pytest.fixture
def hub(demo: tuple[str, Path]) -> LiveHub:
    h = LiveHub(SessionStore(load_config()))
    h.activate(demo[0])
    return h


def test_run_skips_excluded_items_and_plans_section_starts() -> None:
    run = build_run(parse_session(PLAN), None)
    ids = [it["id"] for it in run["items"]]
    assert "act-pairs" not in ids  # "In this session" off
    assert len(ids) == 17
    starts = [s["planned_start"] for s in run["sections"]]
    assert starts == [0, 20, 60, 70, 110]
    assert run["sections"][2]["has_break"] is True
    brk = next(it for it in run["items"] if it["kind"] == "break")
    assert brk["title"] == "Coffee break" and brk["timer"]["start"] == "on_enter"
    # an untitled slide without slides.json falls back to "Slide <id>"
    assert run["items"][0]["title"] == "Slide 101" and run["items"][0]["slide_missing"] is True


def test_movement_and_bounds(hub: LiveHub) -> None:
    assert hub.index == 0 and hub.current()["title"] == "Welcome to the workshop"
    run_action(hub, "next")
    assert hub.current()["id"] == "act-map"
    run_action(hub, "prev")
    run_action(hub, "prev")  # clamps at the first item
    assert hub.index == 0
    run_action(hub, "goto_section", "4")
    assert hub.current()["section_id"] == "sec-agreement"
    run_action(hub, "goto", "999")
    assert hub.index == len(hub.items) - 1
    with pytest.raises(LiveError) as err:
        run_action(hub, "goto_section", "9")
    assert err.value.code == "no_section"
    with pytest.raises(LiveError) as err:
        run_action(hub, "nope")
    assert err.value.code == "unknown_action"


def test_blackout_and_names_toggle(hub: LiveHub) -> None:
    run_action(hub, "blackout")
    run_action(hub, "names_toggle")
    snap = hub.snapshot()["state"]
    assert snap["blackout"] is True and snap["names"] is True
    run_action(hub, "blackout")
    assert hub.snapshot()["state"]["blackout"] is False


def test_on_enter_timer_starts_when_the_break_opens(hub: LiveHub) -> None:
    brk = next(it for it in hub.items if it["kind"] == "break")
    hub.goto(brk["index"])
    t = hub.timers[brk["id"]]
    assert t.running_since is not None and t.total == 600


def test_timer_toggle_add_minute_reset(hub: LiveHub) -> None:
    with pytest.raises(LiveError) as err:
        run_action(hub, "timer_toggle")  # the first slide has no timer
    assert err.value.code == "no_timer"
    hub.goto(1)  # act-map: 120 s, starts with the capture
    run_action(hub, "timer_toggle")
    t = hub.timers["act-map"]
    assert t.running_since is not None
    run_action(hub, "timer_toggle")
    assert t.running_since is None and 119 <= t.remaining(now_ms()) <= 120
    run_action(hub, "timer_add_minute")
    assert t.total == 180
    run_action(hub, "timer_reset")
    assert "act-map" not in hub.timers


def test_timer_end_behaviours(hub: LiveHub) -> None:
    sent: list[dict] = []
    client = hub.connect("presenter")
    client.push = sent.append  # type: ignore[method-assign]
    # chime: the break's timer
    brk = next(it for it in hub.items if it["kind"] == "break")
    hub.goto(brk["index"])
    hub.timers[brk["id"]].running_since = now_ms() - 601_000
    hub._timer_end(brk["id"])
    assert hub.timers[brk["id"]].done is True
    assert any(m.get("type") == "chime" for m in sent)
    # advance: only while the item is on stage
    item = hub.items[3]
    item["timer"] = {"enabled": True, "seconds": 5, "start": "manual", "show_on": "stage", "end": "advance"}
    hub.goto(3)
    hub.timer_toggle()
    hub.timers[item["id"]].running_since = now_ms() - 6_000
    hub._timer_end(item["id"])
    assert hub.index == 4
    # a minute added after the end restarts a fresh running minute
    hub.goto(3)
    hub.timer_add_minute()
    t = hub.timers[item["id"]]
    assert t.done is False and t.running_since is not None and t.total == 60


def test_clock_start_records_section_entries(hub: LiveHub) -> None:
    hub.next()
    assert hub.section_entered == {}  # the clock is not running yet
    hub.clock_start()
    assert list(hub.section_entered) == ["sec-welcome"]
    run_action(hub, "goto_section", "2")
    assert "sec-readme" in hub.section_entered
    hub.clock_reset()
    assert hub.clock_started_at is None and hub.section_entered == {}


def test_state_and_events_survive_a_restart(hub: LiveHub, demo: tuple[str, Path]) -> None:
    sid, folder = demo
    hub.clock_start()
    hub.goto(5)
    hub.toggle_blackout()
    events = [json.loads(line) for line in (folder / "live" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events][:3] == ["session_live", "clock_start", "item"]
    fresh = LiveHub(SessionStore(load_config()))
    fresh.activate(sid)
    assert fresh.index == 5 and fresh.blackout is True and fresh.clock_started_at == hub.clock_started_at


def test_plan_save_reloads_and_keeps_the_current_item(hub: LiveHub, demo: tuple[str, Path]) -> None:
    sid, _ = demo
    hub.goto(4)
    current = hub.current()["id"]
    session = hub.store.load(sid)
    session.sections[0].items[0].include = False  # drop an item before the current one
    hub.store.save(sid, session)
    hub._reload(sid)
    assert hub.current()["id"] == current and hub.index == 3


def test_ws_keeps_stage_and_presenter_in_sync(client, demo: tuple[str, Path]) -> None:
    sid, _ = demo
    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    with client.websocket_connect("/ws?role=stage") as stage, client.websocket_connect("/ws?role=presenter") as presenter:
        for ws in (stage, presenter):
            assert ws.receive_json()["type"] == "plan"
            assert ws.receive_json()["type"] == "state"
        stage.send_json({"type": "hello", "w": 1920, "h": 1080})
        presenter.send_json({"type": "action", "action": "next"})

        def until(ws, pred):
            for _ in range(20):
                m = ws.receive_json()
                if m["type"] == "state" and pred(m["state"]):
                    return m["state"]
            raise AssertionError("state never arrived")

        s = until(stage, lambda st: st["index"] == 1)
        assert s["item_id"] == "act-map"
        p = until(presenter, lambda st: st["index"] == 1 and st["stages"] == [{"w": 1920, "h": 1080}])
        assert p["item_id"] == "act-map"
        presenter.send_json({"type": "action", "action": "timer_toggle"})
        assert until(stage, lambda st: "act-map" in st["timers"])["timers"]["act-map"]["running_since"]
        stage.send_json({"type": "action", "action": "bogus"})
        for _ in range(10):
            m = stage.receive_json()
            if m["type"] == "error":
                assert m["code"] == "unknown_action"
                break
        else:
            raise AssertionError("no error message")


def test_rest_actions_and_errors(client, demo: tuple[str, Path]) -> None:
    r = client.post("/api/live/action", json={"action": "next"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_live"
    assert client.post("/api/live/activate", json={"session": "nope"}).status_code == 404
    client.post("/api/live/activate", json={"session": demo[0]})
    assert client.post("/api/live/action", json={"action": "next"}).json() == {"ok": True, "action": "next"}
    assert client.get("/api/live").json()["state"]["index"] == 1
    ids = {a["id"] for a in client.get("/api/live/actions").json()["actions"]}
    assert {"next", "prev", "blackout", "timer_toggle", "goto_section"} <= ids
    assert client.post("/api/live/deactivate").json() == {"active": False}
    assert client.get("/api/live").json()["plan"]["active"] is False


def test_one_line_joins_the_stage_line_breaks() -> None:
    # A title typed with "\n" breaks on the stage; lists and the results show it on one line.
    assert one_line("What switches\nthis group off?") == "What switches this group off?"
    assert one_line("Two\nlines ") == "Two lines"
    assert one_line("") == ""


def test_notes_from_the_plan_replace_the_slide_notes() -> None:
    meta = {"slides": [{"slide_id": 7, "title": "Hello", "notes": "From the deck", "file": "slide-7.png"}]}
    s = parse_session({"title": "x", "sections": [{"name": "S", "items": [
        {"kind": "slide", "slide_id": 7}, {"kind": "slide", "slide_id": 7, "notes": "Mine"},
        {"kind": "break", "notes": "Stretch"}, {"kind": "activity", "type": "feed", "title": "Ideas"}]}]})
    notes = [(it["notes"], it["notes_own"]) for it in build_run(s, meta)["items"]]
    assert notes == [("From the deck", False), ("Mine", True), ("Stretch", True), ("", False)]
    from src.sessions.model import dump_session
    dumped = [i for sec in dump_session(s)["sections"] for i in sec["items"]]
    assert "notes" not in dumped[0] and dumped[1]["notes"] == "Mine"
