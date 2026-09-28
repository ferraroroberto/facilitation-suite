"""A synthetic quiz run (#55): the demo session plus a quiz, played through the real engine.

``QUIZ_SECTION`` adds a quiz (fake questions) as the demo plan's last section.
``generate`` plays it through a real ``LiveHub`` + ``QuizService`` on a fake
clock — six phone players and two chat players (``source="chat"``, answering
through ``answer_trusted`` the way the chat fallback will), one of them kicked
mid-game, a too-late answer, then a replay (run 2) that stops after two
questions — and returns exactly what the service wrote: the ``quiz.jsonl``
records and the quiz's ``events.jsonl`` lines. Nicknames are made up.

The committed ``quiz.jsonl`` and ``quiz-events.jsonl`` next to this file are
its output (a unit test keeps them in step with the engine); regenerate with

    python -m tests.fixtures.quiz_run

``write_quiz_run(folder)`` adds them to a finished demo run
(``demo.write_demo_run``): the quiz section in ``session.yaml``,
``live/quiz.jsonl``, and the quiz's events just before the session closed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from unittest import mock

import yaml

from tests.fixtures.demo import PLAN, RUN_START_MS, build_demo_session

HERE = Path(__file__).resolve().parent
QUIZ_JSONL = HERE / "quiz.jsonl"
QUIZ_EVENTS = HERE / "quiz-events.jsonl"
QUIZ_T0 = RUN_START_MS + 3_600_000  # an hour into the demo run: after its last item

QUIZ_SECTION: dict[str, Any] = {"id": "sec-quiz", "name": "Space quiz", "minutes": 15, "items": [
    {"kind": "activity", "id": "qz-lobby", "type": "quiz_lobby", "options": {"title": "Space quiz"}},
    {"kind": "activity", "id": "qz-red", "type": "quiz", "question": "Which planet is known as the red planet?",
     "options": {"answer_1": "Venus", "answer_2": "Mars", "answer_3": "Jupiter", "answer_4": "Saturn",
                 "correct": "2", "time_limit": 20, "points": "standard"}},
    {"kind": "activity", "id": "qz-rings", "type": "quiz", "question": "Which of these planets have rings?",
     "options": {"answer_1": "Saturn", "answer_2": "Mars", "answer_3": "Uranus",
                 "correct": "1,3", "time_limit": 10, "points": "double"}},
    {"kind": "activity", "id": "qz-moons", "type": "quiz", "question": "How many moons does Mars have?",
     "options": {"answer_1": "None", "answer_2": "One", "answer_3": "Two", "answer_4": "Four",
                 "correct": "3", "time_limit": 30, "points": "standard"}},
    {"kind": "activity", "id": "qz-sun", "type": "quiz", "question": "Is the Sun a star?",
     "options": {"answer_1": "Yes", "answer_2": "No", "correct": "1", "time_limit": 10, "points": "none"}},
    {"kind": "activity", "id": "qz-podium", "type": "quiz_podium"},
]}
PLAN_WITH_QUIZ: dict[str, Any] = {**PLAN, "sections": [*PLAN["sections"], QUIZ_SECTION]}

PHONE = ["Comet", "Nebula", "Pulsar", "Quasar", "Orbit", "Meteor"]
CHAT = ["Zenith", "Aurora"]
# Per question, in answer order: (nickname, choice, ms after the question opened). A phone reports
# that time itself (the buttons rendered then) and its answer reaches the server ``LATENCY_MS`` later;
# a chat answer (``CHAT``) is timed by the server when it arrives.
RUN1: dict[str, list[tuple[str, int, int]]] = {
    "qz-red": [("Quasar", 2, 400), ("Comet", 2, 1200), ("Meteor", 2, 1800), ("Nebula", 2, 2400),
               ("Pulsar", 1, 3100), ("Zenith", 2, 4200), ("Orbit", 3, 5000), ("Aurora", 4, 6500)],
    "qz-rings": [("Nebula", 3, 1500), ("Comet", 1, 2000), ("Quasar", 2, 3000), ("Pulsar", 1, 4200),
                 ("Zenith", 3, 5600)],
    "qz-moons": [("Quasar", 1, 1000), ("Nebula", 3, 2500), ("Comet", 3, 6000), ("Aurora", 3, 7300),
                 ("Pulsar", 3, 9000), ("Zenith", 2, 10400), ("Orbit", 3, 12000)],
    "qz-sun": [("Comet", 1, 800), ("Nebula", 1, 900), ("Pulsar", 2, 1100), ("Orbit", 1, 3000), ("Aurora", 1, 4100)],
}
RUN2: dict[str, list[tuple[str, int, int]]] = {
    "qz-red": [("Comet", 2, 900), ("Zenith", 2, 2600)],
    "qz-rings": [("Comet", 3, 1300), ("Zenith", 1, 3300)],
}
KICKED = "Meteor"  # removed by the host after the first question
LATENCY_MS = 300  # a phone's answer reaches the server this long after the tap


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def generate(folder: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Play the quiz on the demo session written into ``folder`` (the env vars must point at a
    config whose ledger lists it); returns (``quiz.jsonl`` records, the quiz's events)."""
    from src.config import load_config
    from src.jsonl import read_jsonl
    from src.live.hub import LiveHub
    from src.quiz.service import QUIZ_FILE, QuizService
    from src.sessions.store import SESSION_FILE, SessionStore, session_id

    add_quiz_section(folder)
    clock = Clock(QUIZ_T0)

    class Clocked(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # noqa: ANN401 — datetime's own signature
            return datetime.fromtimestamp(clock.t / 1000, tz or UTC)

    assert (folder / SESSION_FILE).is_file()
    with mock.patch("src.live.hub.datetime", Clocked):
        hub = LiveHub(SessionStore(load_config()))
        quiz = QuizService(hub, clock=clock)
        hub.activate(session_id(folder))
        skip = len(read_jsonl(folder / "live" / "events.jsonl"))

        def goto(item_id: str) -> None:
            clock.t += 30_000
            hub.goto(hub.item_by_id(item_id)["index"])

        def play(answers: dict[str, list[tuple[str, int, int]]], players: dict[str, Any],
                 stop_after: Optional[str] = None) -> None:
            for item_id, rows in answers.items():
                clock.t += 20_000
                hub.next()  # the question opens
                assert hub.current()["id"] == item_id, hub.current()["id"]
                opened = clock.t
                for name, choice, ms in rows:
                    p = players[name]
                    if name in CHAT:
                        clock.t = opened + ms
                        res = quiz.answer_trusted(p.player_id, item_id, choice)
                    else:
                        clock.t = opened + ms + LATENCY_MS
                        res = quiz.answer(p.player_id, p.secret, item_id, choice, elapsed_ms=ms)
                    assert res.state == "accepted" and res.elapsed_ms == ms, (name, item_id, res)
                clock.t += 1_500
                hub.next()  # reveal
                clock.t += 8_000
                hub.next()  # leaderboard
                if item_id == stop_after:
                    return

        goto("qz-lobby")
        players = {n: quiz.join(n) for n in PHONE}
        players |= {n: quiz.join(n, source="chat") for n in CHAT}
        play({"qz-red": RUN1["qz-red"]}, players)
        quiz.kick(players[KICKED].player_id)
        play({k: v for k, v in RUN1.items() if k != "qz-red"}, players)
        late = players["Quasar"]
        assert quiz.answer(late.player_id, late.secret, "qz-sun", 1, elapsed_ms=500).state == "too_late"
        clock.t += 15_000
        hub.next()
        assert hub.current()["id"] == "qz-podium"

        goto("qz-lobby")  # play it again: a new game, run 2
        clock.t += 5_000
        quiz.new_game()
        players2 = {"Comet": quiz.join("Comet"), "Nova": quiz.join("Nova"), "Zenith": quiz.join("Zenith", source="chat")}
        play(RUN2, players2, stop_after="qz-rings")
        goto("qz-podium")  # straight to the podium: the last two questions are never asked
        clock.t += 20_000
    records = read_jsonl(folder / "live" / QUIZ_FILE)
    events = read_jsonl(folder / "live" / "events.jsonl")[skip:]
    return records, events


def add_quiz_section(folder: Path) -> None:
    """Append ``QUIZ_SECTION`` to the demo session's ``session.yaml``."""
    from src.sessions.store import SESSION_FILE

    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    if not any(s.get("id") == QUIZ_SECTION["id"] for s in raw["sections"]):
        raw["sections"].append(QUIZ_SECTION)
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _lines(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def write_quiz_run(folder: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Add the committed quiz run to a finished demo run in ``folder`` (see the module docstring)."""
    from src.jsonl import read_jsonl

    add_quiz_section(folder)
    records, quiz_events = read_jsonl(QUIZ_JSONL), read_jsonl(QUIZ_EVENTS)
    live = folder / "live"
    (live / "quiz.jsonl").write_text(_lines(records), encoding="utf-8")
    events = read_jsonl(live / "events.jsonl")
    closed = [e for e in events if e.get("event") == "session_closed"]
    events = [e for e in events if e.get("event") != "session_closed"]
    assert not events or events[-1]["at"] < quiz_events[0]["at"], "the quiz must start after the demo run"
    end = datetime.fromisoformat(quiz_events[-1]["at"]).timestamp() * 1000 + 60_000
    closed = [{**e, "at": datetime.fromtimestamp(end / 1000, UTC).isoformat(timespec="milliseconds")} for e in closed]
    (live / "events.jsonl").write_text(_lines(events + quiz_events + closed), encoding="utf-8")
    return records, quiz_events


def main() -> int:
    """Regenerate ``quiz.jsonl`` + ``quiz-events.jsonl`` in a throwaway environment."""
    from tests.conftest import write_test_config

    root = Path(tempfile.mkdtemp(prefix="fs-quiz-run-"))
    try:
        write_test_config(root / "config.json", session_root=str(root / "sessions"))
        os.environ.update(FS_CONFIG_PATH=str(root / "config.json"), FS_LEDGER_PATH=str(root / "sessions.local.yaml"),
                          FS_DATA_DIR=str(root / "data"), FS_ENV_PATH=str(root / ".env"))
        _, folder = build_demo_session(root / "sessions" / "quiz", root / "sessions.local.yaml")
        records, events = generate(folder)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    QUIZ_JSONL.write_text(_lines(records), encoding="utf-8", newline="\n")
    QUIZ_EVENTS.write_text(_lines(events), encoding="utf-8", newline="\n")
    print(f"{len(records)} quiz records, {len(events)} events → {QUIZ_JSONL.name}, {QUIZ_EVENTS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
