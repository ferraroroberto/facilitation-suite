"""Activity parsers (pure) and captures: windows, hide, own messages, timers, freeze."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.activities.registry import parser, preview, result_for, value_for
from src.chat.hub import ChatHub
from src.config import load_config
from src.live import capture
from src.live.actions import run_action
from src.live.capture import CaptureService
from src.live.hub import LiveError, LiveHub, now_ms
from src.results.aliases import Aliases, Excluded, seen_names
from src.results.collect import load_results, participation
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


def test_word_cloud_terms_auto_is_the_default() -> None:
    texts = _msgs("no agenda", "miedo a fallar", "creo que el problema son las reuniones eternas", "jajaja")
    assert result_for("word_cloud", {"terms": "auto"}, texts) == result_for("word_cloud", {}, texts)


def test_word_cloud_terms_verbatim_keeps_each_answer_whole() -> None:
    sentence = "the thing i take away is that saying no to meetings gives me back my whole afternoon"
    r = result_for("word_cloud", {"terms": "verbatim"}, _msgs(
        "Saying yes to everything", "  saying   yes to everything! ",  # one entry: case, spaces, end punctuation
        sentence,                                                      # 17 words: still one entry
        "jajaja xd",                                                   # laughter only: dropped
        "no agenda",                                                   # no stopwords in verbatim
    ))
    assert [(w["text"], w["count"]) for w in r["words"]] == [
        ("Saying yes to everything", 2), (sentence, 1), ("no agenda", 1)]  # a tie: the first seen spelling
    assert r["answers"] == 4
    apart = result_for("word_cloud", {"terms": "verbatim", "merge_variants": False}, _msgs("¡Sí!", "Sí", "sí"))
    assert sorted((w["text"], w["count"]) for w in apart["words"]) == [("Sí", 2), ("sí", 1)]


def test_word_cloud_terms_verbatim_shows_the_most_common_original_casing() -> None:
    r = result_for("word_cloud", {"terms": "verbatim"}, _msgs(
        "i stop listening", "I stop listening", "I stop listening!",   # merged: the typed casing wins 2-1
        "Reunión", "reunion", "REUNIONES",                              # merged with accents and a plural: a tie
    ))
    assert [(w["text"], w["count"]) for w in r["words"]] == [("I stop listening", 3), ("Reunión", 3)]


def test_word_cloud_terms_words_always_splits() -> None:
    r = result_for("word_cloud", {"terms": "words", "stopwords": "both"}, _msgs(
        "no agenda",                         # short, still split: "no" is a stopword
        "meetings",
        "long meetings with no agenda",      # a sentence: its words minus stopwords
        "jajaja",
    ))
    assert [(w["key"], w["count"]) for w in r["words"]] == [("agenda", 2), ("meetings", 2), ("long", 1)]
    assert r["answers"] == 3


def test_word_cloud_split_ideas_keeps_each_part_a_phrase() -> None:
    r = result_for("word_cloud", {"split_ideas": True}, _msgs("calm + humour", "calm", "no ego; open mind"))
    assert [(w["key"], w["count"]) for w in r["words"]] == [("calm", 2), ("humour", 1), ("no ego", 1), ("open mind", 1)]
    assert [w["key"] for w in result_for("word_cloud", {}, _msgs("calm + humour"))["words"]] == ["calm humour"]


def test_word_cloud_conjunction_split_only_with_its_switch() -> None:
    texts = _msgs("respeto y empatía", "trust and focus")
    assert [w["key"] for w in result_for("word_cloud", {"split_ideas": True}, texts)["words"]] == [
        "respeto y empatia", "trust and focus"]
    assert [w["key"] for w in result_for("word_cloud", {"split_and": True}, texts)["words"]] == [
        "respeto", "empatia", "trust", "focus"]


@pytest.mark.parametrize(("long_answers", "entries"), [("whole", ["levantar la mano a tiempo con el equipo"]),
                                                       ("keyword", ["levantar"])])
def test_word_cloud_a_long_answer_is_at_most_one_entry(long_answers: str, entries: list[str]) -> None:
    sentence = "Levantar la mano a tiempo con el equipo"
    r = result_for("word_cloud", {"long_answers": long_answers, "stopwords": "es"}, _msgs(sentence))
    assert [w["key"] for w in r["words"]] == entries
    assert len(result_for("word_cloud", {"stopwords": "es"}, _msgs(sentence))["words"]) == 4  # the default still splits


def test_word_cloud_emoticon_only_answer_is_no_entry() -> None:
    r = result_for("word_cloud", {}, _msgs(":P", ":-)", "<3", "genial :D"))
    assert [w["key"] for w in r["words"]] == ["genial"] and r["answers"] == 1


def test_word_cloud_near_spellings_join_a_present_word() -> None:
    near = {"merge_near": True}
    r = result_for("word_cloud", near, _msgs("apoyo", "apoyo", "aooyo", "repetitivas", "ripetitivas"))
    assert [(w["text"], w["count"]) for w in r["words"]] == [("apoyo", 3), ("repetitivas", 2)]
    alone = result_for("word_cloud", near, _msgs("aooyo", "calma"))  # nothing to join: stays as typed
    assert [w["key"] for w in alone["words"]] == ["aooyo", "calma"]
    short = result_for("word_cloud", near, _msgs("pan", "paz", "paz"))  # short words one letter apart never merge
    assert [w["key"] for w in short["words"]] == ["paz", "pan"]
    off = result_for("word_cloud", {}, _msgs("apoyo", "aooyo"))
    assert [w["key"] for w in off["words"]] == ["apoyo", "aooyo"]


def test_word_cloud_report_value_is_the_entries_the_answer_counted_towards() -> None:
    mod = parser("word_cloud")
    opts = {"split_ideas": True, "merge_near": True}
    texts = ["Apoyo + calma", "aooyo", "apoyo"]
    parsed = [mod.parse({"sender": f"P{i}", "text": t}, opts) for i, t in enumerate(texts)]
    result = mod.aggregate(parsed, opts)
    assert [value_for("word_cloud", p, result) for p in parsed] == ["apoyo, calma", "apoyo", "apoyo"]
    assert mod.counted(texts[0], parsed[0], result) == {"entries": ["apoyo", "calma"], "whole": True}
    assert mod.counted(texts[1], parsed[1], result)["entries"] == ["apoyo"]


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


def test_own_messages_count_only_while_rehearsing(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    assert live.snapshot()["state"]["count_own"] is False  # off by default
    run_action(live, "capture_toggle")
    _say(chat, ("You", "reuniones"), ("Ana", "silos"))
    state = live.snapshot()["state"]["capture"]
    assert state["answers"] == 1 and {w["key"] for w in state["result"]["words"]} == {"silos"}

    run_action(live, "capture_count_own")  # rehearsal: my own typing counts, the earlier message too
    snap = live.snapshot()["state"]
    assert snap["count_own"] is True
    assert snap["capture"]["answers"] == 2 and {w["key"] for w in snap["capture"]["result"]["words"]} == {"reuniones", "silos"}
    run_action(live, "capture_toggle")  # the frozen result counts it as well
    frozen = json.loads((folder / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert [a["sender"] for a in frozen["answers"]] == ["You", "Ana"] and frozen["result"]["answers"] == 2

    run_action(live, "capture_count_own")  # off again: back to the live rule
    assert [m["text"] for m in cap.messages_for("act-kryptonite")] == ["silos"]


def test_counting_own_messages_starts_off_after_a_restart_and_an_activation(svc) -> None:
    live, _, cap, _ = svc
    sid = live.session_id
    run_action(live, "capture_count_own")
    assert cap.count_own is True
    live.deactivate()
    live.activate(sid)  # activating a session: off
    assert cap.count_own is False and live.snapshot()["state"]["count_own"] is False
    run_action(live, "capture_count_own")
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")  # writes captures.json
    assert "count_own" not in (live.folder / "live" / "captures.json").read_text(encoding="utf-8")  # never saved
    live2 = LiveHub(SessionStore(load_config()))  # a restarted server: off
    cap2 = CaptureService(live2, ChatHub(live2))
    live2.activate(sid)
    assert cap2.count_own is False


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


def test_a_failed_captures_json_warning_goes_with_the_next_good_write(svc, monkeypatch: pytest.MonkeyPatch) -> None:
    live, chat, cap, folder = svc
    real = capture.atomic_write_text
    failures = ["captures.json"]

    def flaky(path: Path, text: str) -> None:
        if path.name in failures:
            failures.remove(path.name)
            raise PermissionError(13, "The file is in use by another process", str(path))
        real(path, text)

    monkeypatch.setattr(capture, "atomic_write_text", flaky)
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    assert live.write_error == "Could not write captures.json in the session folder — kept in memory"
    run_action(live, "capture_toggle")
    assert live.write_error is None
    saved = json.loads((folder / "live" / "captures.json").read_text(encoding="utf-8"))
    assert [w[1] is not None for w in saved["windows"]["act-enemy"]] == [True]  # the window, opened and closed


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


def test_a_reset_switches_off_counting_own_messages(svc) -> None:
    live, _, cap, _ = svc
    _goto(live, "act-kryptonite")
    run_action(live, "capture_count_own")  # rehearsing: turn it on
    assert cap.count_own is True
    run_action(live, "session_reset")  # "Start the session over" — real run: back off
    assert cap.count_own is False and live.snapshot()["state"]["count_own"] is False


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


def test_an_unreadable_frozen_capture_and_a_parser_crash_are_logged(svc, monkeypatch, caplog) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "reuniones"))

    def boom(message: dict[str, Any], options: dict[str, Any]) -> None:
        raise ValueError("plug-in bug")

    monkeypatch.setattr(parser("word_cloud"), "parse", boom)
    with caplog.at_level("ERROR", logger="src.live.capture"):
        run_action(live, "capture_toggle")  # stop -> freeze
    assert "parse failed on message" in caplog.text
    frozen = json.loads((folder / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert [a["parsed"] for a in frozen["answers"]] == [None]  # unparsed, not a failed freeze

    caplog.clear()
    (folder / "live" / "captures" / "act-kryptonite.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="src.live.capture"):
        assert cap.frozen("act-kryptonite") is None
    assert "frozen record unreadable" in caplog.text


# --------------------------------------- excluded senders, capture scope, closing


def _frozen(folder: Path, item_id: str) -> dict[str, Any]:
    return json.loads((folder / "live" / "captures" / f"{item_id}.json").read_text(encoding="utf-8"))


def _setting(live: LiveHub, on: bool) -> None:
    """Switch the session's ``stop_capture_on_leave`` the way a saved plan does."""
    live.store.edit(live.session_id, lambda s: setattr(s, "stop_capture_on_leave", on))
    live._reload(live.session_id)


def test_an_excluded_sender_counts_nowhere_and_comes_back(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "reuniones"), ("Co Host", "ten minutes break"), ("Sam", "silos"))
    run_action(live, "exclude_sender", "Co Host")
    snap = live.snapshot()["state"]
    assert snap["excluded_senders"] == ["Co Host"]
    assert snap["capture"]["answers"] == 2 and snap["capture"]["excluded"] == 1
    assert {w["key"] for w in snap["capture"]["result"]["words"]} == {"reuniones", "silos"}
    assert SessionStore(load_config()).load(live.session_id).excluded_senders == ["Co Host"]  # saved in session.yaml

    run_action(live, "capture_toggle")
    frozen = _frozen(folder, "act-kryptonite")
    assert frozen["result"]["answers"] == 2
    assert [bool(a.get("excluded")) for a in frozen["answers"]] == [False, True, False]  # kept, flagged

    run_action(live, "include_sender", "co  host")  # case and spacing are not another sender
    assert live.snapshot()["state"]["excluded_senders"] == []
    assert _frozen(folder, "act-kryptonite")["result"]["answers"] == 3  # the stopped capture was frozen again
    run_action(live, "exclude_sender", "Co Host")
    assert _frozen(folder, "act-kryptonite")["result"]["answers"] == 2


def test_an_excluded_sender_is_excluded_with_every_name_merged_into_theirs(svc) -> None:
    live, chat, cap, folder = svc
    (folder / "aliases.yaml").write_text(yaml.safe_dump({"Co Host": ["iPhone de Co Host"]}), encoding="utf-8")
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    run_action(live, "exclude_sender", "Co Host")
    _say(chat, ("iPhone de Co Host", "hello from the phone"), ("Ana", "silos"))
    assert [m["sender"] for m in cap.messages_for("act-enemy")] == ["Ana"]


def test_results_and_participation_leave_an_excluded_sender_out(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "silos"), ("Co Host", "back in five"))
    run_action(live, "capture_toggle")
    st = SessionStore(load_config())
    before = load_results(folder, st.load(live.session_id))["activities"][0]
    assert (before["answers"], before["people"], before["excluded"]) == (2, 2, 0)

    st.edit(live.session_id, lambda s: setattr(s, "excluded_senders", ["Co Host"]))  # excluded after the freeze
    session = st.load(live.session_id)
    data = load_results(folder, session)
    after = data["activities"][0]
    assert (after["answers"], after["people"], after["excluded"], after["hidden"]) == (1, 1, 1, 0)
    assert (before["summary"], after["summary"]) == ("2 cards", "1 card")  # recounted without the excluded sender
    assert [(r["sender"], r["hidden"], r["excluded"]) for r in after["answer_rows"]] == [("Ana", False, False), ("Co Host", True, True)]
    people = participation(data["activities"], chat.messages, Aliases(), Excluded(session.excluded_senders))
    assert [p["name"] for p in people] == ["Ana"]
    assert seen_names(data["activities"], chat.messages, Excluded(session.excluded_senders)) == ["Ana"]


def test_leaving_an_activity_stops_and_freezes_its_capture_with_the_setting_on(svc) -> None:
    live, chat, cap, folder = svc
    _setting(live, True)
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "silos"))
    live.goto(0)  # on to a slide
    assert cap.status("act-enemy") == "stopped" and cap.active_item() is None
    assert _frozen(folder, "act-enemy")["result"]["answers"] == 1
    _say(chat, ("Ana", "banter during the break"))
    assert [m["text"] for m in cap.messages_for("act-enemy")] == ["silos"]


def test_leaving_an_activity_leaves_its_capture_open_with_the_setting_off(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    live.goto(0)
    assert cap.status("act-enemy") == "live"
    assert not (folder / "live" / "captures" / "act-enemy.json").exists()


def test_a_timer_ending_with_hide_stops_the_capture_only_with_the_setting_on(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    item = live.item_by_id("act-kryptonite")
    cap._on_timer_end(item, "hide")
    assert cap.status("act-kryptonite") == "live"
    _setting(live, True)
    cap._on_timer_end(item, "hide")
    assert cap.status("act-kryptonite") == "stopped"


def test_a_capture_still_live_when_the_session_closes_is_frozen(svc) -> None:
    live, chat, cap, folder = svc
    _goto(live, "act-enemy")
    run_action(live, "capture_toggle")
    _say(chat, ("Ana", "silos"))
    live.deactivate()
    frozen = _frozen(folder, "act-enemy")
    assert frozen["result"]["answers"] == 1 and frozen["windows"][-1][1] is not None
    names = [json.loads(line)["event"] for line in (folder / "live" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert names.index("capture_stop") < names.index("session_closed")
