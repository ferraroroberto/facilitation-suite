"""Activity parsers (pure) and captures: windows, hide, own messages, timers, freeze."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from src.activities.registry import parser, preview, result_for
from src.chat.hub import ChatHub
from src.config import load_config
from src.live.actions import run_action
from src.live.capture import CaptureService
from src.live.hub import LiveError, LiveHub, now_ms
from src.sessions.store import SessionStore
from tests.fixtures.demo import build_demo_session


def _msgs(*texts: str, senders: list[str] | None = None) -> list[dict[str, Any]]:
    who = senders or [f"P{i}" for i in range(len(texts))]
    return [{"id": i + 1, "sender": who[i], "text": t, "time": "18:41", "received_at": 0} for i, t in enumerate(texts)]


# ------------------------------------------------------------------ word cloud


def test_word_cloud_groups_case_accents_and_plurals() -> None:
    r = result_for("word_cloud", {}, _msgs("Reunión", "reuniones", "REUNIONES", "perfeccionismo", "reunion"))
    top = r["words"][0]
    assert top["key"] == "reunion" and top["count"] == 4 and top["text"] == "reuniones"
    assert r["answers"] == 5 and r["people"] == 5


def test_word_cloud_phrases_sentences_and_fillers() -> None:
    r = result_for("word_cloud", {"stopwords": "es"}, _msgs(
        "miedo a fallar",                                  # ≤ 3 words: one phrase
        "creo que el problema son las reuniones eternas",  # a sentence: words minus stopwords
        "jajaja", "xd",                                    # fillers only: nothing
    ))
    keys = {w["key"] for w in r["words"]}
    assert "miedo a fallar" in keys
    assert {"creo", "problema", "reuniones", "eternas"} <= keys and "que" not in keys
    assert r["answers"] == 2


def test_word_cloud_without_merging_keeps_spellings_apart() -> None:
    r = result_for("word_cloud", {"merge_variants": False}, _msgs("Sol", "sol"))
    assert sorted(w["text"] for w in r["words"]) == ["Sol", "sol"]


# ----------------------------------------------------------------------- scale


@pytest.mark.parametrize(("text", "value"), [("4", 4), ("un 4 hoy", 4), ("4/5", 4), ("7", None), ("3.5", None),
                                             ("sunny spells", 4), ("SUN!", 5), ("a bit of rain", 2), ("nothing", None)])
def test_scale_numbers_and_keywords(text: str, value: int | None) -> None:
    mod = parser("scale")
    got = mod.parse({"sender": "A", "text": text}, {"min": 1, "max": 5, "keywords": "storm, rain, cloudy, sunny spells, sun"})
    assert (got or {}).get("value") == value


def test_scale_counts_one_vote_per_person_and_averages() -> None:
    r = result_for("scale", {}, _msgs("2", "4", "5", senders=["Ana", "Ana", "Sam"]))
    assert [b["count"] for b in r["bins"]] == [0, 0, 0, 1, 1]  # Ana's 4 replaced her 2
    assert r["average"] == 4.5 and r["people"] == 2 and r["answers"] == 3


# --------------------------------------------------------------- cards · feed


def test_cards_keep_the_latest_and_feed_is_newest_first() -> None:
    cards = result_for("cards", {"max_cards": 2}, _msgs("one", "two", "three"))
    assert [c["text"] for c in cards["cards"]] == ["two", "three"]
    feed = result_for("feed", {}, _msgs("one", "two", "", "three"))
    assert [m["text"] for m in feed["items"]] == ["three", "two", "one"]


def test_every_capture_type_previews_from_its_samples() -> None:
    for t in ("word_cloud", "scale", "cards", "feed"):
        assert preview(t, {})["answers"] > 0


# -------------------------------------------------------------------- captures


@pytest.fixture
def svc(isolated_env: Path) -> tuple[LiveHub, ChatHub, CaptureService, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "cap", isolated_env / "sessions.local.yaml")
    live = LiveHub(SessionStore(load_config()))
    chat = ChatHub(live)
    cap = CaptureService(live, chat)
    live.activate(sid)
    return live, chat, cap, folder


def _say(chat: ChatHub, *rows: tuple[str, str]) -> None:
    time.sleep(0.003)  # messages arrive strictly after the action before them
    chat.ingest("", [{"sender": s, "text": t, "time": "18:41", "received_at": now_ms()} for s, t in rows],
                baseline=False, source="zoom")
    time.sleep(0.003)


def _goto(live: LiveHub, item_id: str) -> None:
    live.goto(live.item_by_id(item_id)["index"])


def test_capture_counts_only_inside_its_windows(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")  # word cloud, timer with the capture, stop at the end
    _say(chat, ("Sam", "before the start"))
    with pytest.raises(LiveError):
        live.goto(0)
        run_action(live, "capture_toggle")  # a slide cannot capture
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    assert cap.status("act-kryptonite") == "live"
    assert live.timers["act-kryptonite"].running_since is not None  # the timer started with the capture
    _say(chat, ("Ana", "reuniones"), ("You", "the prompt"), ("Sam", "perfeccionismo"))
    state = live.snapshot()["state"]["capture"]
    assert state["answers"] == 2 and state["people"] == 2  # "You" never counts
    assert {w["key"] for w in state["result"]["words"]} == {"reuniones", "perfeccionismo"}

    run_action(live, "capture_toggle")  # stop: frozen
    assert cap.status("act-kryptonite") == "stopped"
    assert live.timers["act-kryptonite"].running_since is None  # stopped by hand: the timer pauses
    frozen = json.loads((folder / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert [a["text"] for a in frozen["answers"]] == ["reuniones", "perfeccionismo"]
    assert frozen["result"]["answers"] == 2

    _say(chat, ("Ana", "while stopped"))
    run_action(live, "capture_toggle")  # reopen: a new window
    _say(chat, ("Robin", "cansancio"))
    run_action(live, "capture_toggle")
    texts = [m["text"] for m in cap.messages_for("act-kryptonite")]
    assert texts == ["reuniones", "perfeccionismo", "cansancio"]


def test_hide_and_unhide_a_message(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "can you hear me?"), ("Sam", "silos"))
    noise = chat.messages[-2]["id"]
    run_action(live, "hide_message", str(noise))
    assert [c["text"] for c in live.snapshot()["state"]["capture"]["result"]["cards"]] == ["silos"]
    assert live.snapshot()["state"]["capture"]["hidden"] == 1
    run_action(live, "capture_toggle")
    frozen = json.loads((folder / "live" / "captures" / "act-enemy.json").read_text(encoding="utf-8"))
    assert [a["hidden"] for a in frozen["answers"]] == [True, False]
    run_action(live, "unhide_message", str(noise))  # re-freezes the stopped capture
    frozen = json.loads((folder / "live" / "captures" / "act-enemy.json").read_text(encoding="utf-8"))
    assert frozen["result"]["answers"] == 2


def test_timer_end_stops_the_capture_and_state_survives_a_restart(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    live.timers["act-kryptonite"].running_since = now_ms() - 181_000
    live._timer_end("act-kryptonite")  # end: stop_capture
    assert cap.status("act-kryptonite") == "stopped"
    assert (folder / "live" / "captures" / "act-kryptonite.json").is_file()
    # a restarted server reads captures.json back
    live2 = LiveHub(SessionStore(load_config()))
    cap2 = CaptureService(live2, ChatHub(live2))
    live2.activate(live.session_id)
    assert cap2.status("act-kryptonite") == "stopped"


def test_a_running_timer_always_captures_and_a_reset_stops_it(svc) -> None:
    live, chat, cap, _ = svc
    _goto(live, "act-kryptonite")  # timer with the capture, stop_capture at 00:00
    run_action(live, "timer_toggle")  # starting the timer opens the capture
    assert cap.status("act-kryptonite") == "live"
    live.timers["act-kryptonite"].running_since = now_ms() - 181_000
    live._timer_end("act-kryptonite")
    assert cap.status("act-kryptonite") == "stopped"
    run_action(live, "timer_add_minute")  # one more minute after 00:00: the capture goes on
    assert cap.status("act-kryptonite") == "live" and live.timers["act-kryptonite"].running_since is not None
    _say(chat, ("Ana", "late answer"))
    assert "late answer" in [m["text"] for m in cap.messages_for("act-kryptonite")]
    run_action(live, "timer_toggle")  # paused: answers still count
    assert cap.status("act-kryptonite") == "live" and live.timers["act-kryptonite"].running_since is None
    run_action(live, "timer_reset")  # a reset stops the capture instead of leaving it stuck
    assert cap.status("act-kryptonite") == "stopped" and "act-kryptonite" not in live.timers
    # a break's timer has nothing to capture
    _goto(live, "brk-coffee")
    assert cap.status("brk-coffee") == "idle"


def test_a_reset_starts_over_and_keeps_the_run_aside(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "clock_start")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "meetings"))
    run_action(live, "capture_toggle")
    rev = live.plan_rev
    run_action(live, "session_reset")
    assert (live.index, live.clock_started_at, live.timers, live.plan_rev) == (0, None, {}, rev + 1)
    assert cap.status("act-kryptonite") == "idle" and chat.messages == []
    kept = [d for d in folder.iterdir() if d.name.startswith("live-")]
    assert len(kept) == 1 and (kept[0] / "captures" / "act-kryptonite.json").is_file() and (kept[0] / "chat.jsonl").is_file()
    events = [json.loads(line) for line in (folder / "live" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "session_live" and events[0]["reset"] == kept[0].name
    # the new run captures from scratch
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    _say(chat, ("Sam", "focus"))
    assert [m["text"] for m in cap.messages_for("act-kryptonite")] == ["focus"]


def test_one_capture_at_a_time(svc) -> None:
    live, _, cap, _ = svc
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    _goto(live, "act-ideas")
    run_action(live, "capture_toggle")
    assert cap.status("act-enemy") == "stopped" and cap.status("act-ideas") == "live"


def test_an_activity_opens_with_its_own_show_names(svc) -> None:
    live, _, _, _ = svc
    _goto(live, "act-enemy")  # cards: show_names defaults to on
    assert live.names is True
    _goto(live, "act-kryptonite")  # word cloud: show_names off in the plan
    assert live.names is False


def test_space_captures_else_runs_the_timer(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "space")  # an activity that takes answers: the capture
    assert cap.status("act-kryptonite") == "live"
    run_action(live, "space")
    assert cap.status("act-kryptonite") == "stopped"
    readme = next(it for it in live.items if it["title"] == "Personal readme instructions")
    live.goto(readme["index"])  # a slide with a timer: Space starts and pauses it
    run_action(live, "space")
    assert live.timers[readme["id"]].running_since is not None
    run_action(live, "space")
    assert live.timers[readme["id"]].running_since is None
    live.goto(0)  # neither a capture nor a timer
    with pytest.raises(LiveError) as err:
        run_action(live, "space")
    assert err.value.code == "nothing_to_start"
