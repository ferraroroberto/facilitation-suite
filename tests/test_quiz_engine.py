"""The quiz game engine (#51): scoring, players, answers, phases, ``next`` and replay after a restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

from src.config import load_config
from src.jsonl import read_jsonl
from src.live.actions import run_action
from src.live.hub import LiveError, LiveHub
from src.quiz.engine import GRACE_MS, Game, clean_name, points
from src.quiz.model import QuizError, QuizQuestion
from src.quiz.service import QUIZ_FILE, QuizService
from src.sessions.store import SESSION_FILE, SessionStore
from tests.fixtures.demo import build_demo_session

T0 = 1_800_000_000_000

QUIZ_SECTION: dict[str, Any] = {"id": "sec-quiz", "name": "Planets quiz", "minutes": 10, "items": [
    {"kind": "activity", "id": "qz-lobby", "type": "quiz_lobby", "options": {"title": "Planets"}},
    {"kind": "activity", "id": "qz-1", "type": "quiz", "question": "Which planet is the red one?",
     "options": {"answer_1": "Venus", "answer_2": "Mars", "answer_3": "Jupiter", "answer_4": "Saturn",
                 "correct": "2", "time_limit": 20, "points": "standard"}},
    {"kind": "activity", "id": "qz-2", "type": "quiz", "question": "Which have rings?",
     "options": {"answer_1": "Saturn", "answer_2": "Mars", "answer_3": "Uranus",
                 "correct": "1,3", "time_limit": 10, "points": "double"}},
    # Options left at their defaults are not written: correct "1", 20 s, standard points.
    {"kind": "activity", "id": "qz-3", "type": "quiz", "question": "Is the Sun a star?",
     "options": {"answer_1": "Yes", "answer_2": "No"}},
    {"kind": "activity", "id": "qz-podium", "type": "quiz_podium"},
    {"kind": "activity", "id": "qz-orphan", "type": "quiz", "question": "A question outside any game",
     "options": {"answer_1": "One", "answer_2": "Two"}},
]}


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> int:
        return self.t


@pytest.fixture
def quiz_session(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "quiz", isolated_env / "sessions.local.yaml")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    raw["sections"].insert(1, QUIZ_SECTION)
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return sid, folder


def make_rig(sid: str, clock: Clock) -> tuple[LiveHub, QuizService]:
    hub = LiveHub(SessionStore(load_config()))
    quiz = QuizService(hub, clock=clock)
    hub.activate(sid)
    return hub, quiz


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rig(quiz_session: tuple[str, Path], clock: Clock) -> tuple[LiveHub, QuizService]:
    return make_rig(quiz_session[0], clock)


def goto_id(hub: LiveHub, item_id: str) -> None:
    hub.goto(hub.item_by_id(item_id)["index"])


def state(hub: LiveHub) -> dict[str, Any]:
    return hub.snapshot()["state"]["quiz"]


def at_lobby_with(hub: LiveHub, quiz: QuizService, *names: str) -> list[Any]:
    goto_id(hub, "qz-lobby")
    return [quiz.join(n) for n in names]


async def bound_rig(sid: str, clock: Clock) -> tuple[LiveHub, QuizService]:
    """A rig whose hub is bound to the running loop, so ``call_later`` (the early-close and
    time-up timers) actually schedules — unlike ``rig``/``make_rig``, which run with no loop."""
    hub = LiveHub(SessionStore(load_config()))
    quiz = QuizService(hub, clock=clock)
    hub.bind(asyncio.get_running_loop())
    hub.activate(sid)
    return hub, quiz


async def wait_for_phase(hub: LiveHub, phase: str, ceiling_s: float) -> bool:
    """Poll ``state(hub)["phase"]`` up to ``ceiling_s``; ``True`` once it matches ``phase``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ceiling_s
    while state(hub)["phase"] != phase and loop.time() < deadline:
        await asyncio.sleep(0.01)
    return state(hub)["phase"] == phase


# ---- scoring (Kahoot's rule, see engine.py) ----

Q20 = QuizQuestion("Q?", ["a", "b", "c", "d"], [2], 20, "standard")


@pytest.mark.parametrize(("question", "correct", "elapsed", "expected"), [
    (Q20, True, 0, 1000),
    (Q20, True, 499, 1000),  # inside the first half second: full points
    (Q20, True, 1000, 975),
    (Q20, True, 3333, 917),  # 916.675 rounds half up
    (Q20, True, 10_000, 750),
    (Q20, True, 20_000, 500),
    (Q20, True, 99_000, 500),  # never below half
    (Q20, False, 1000, 0),
    (QuizQuestion("Q?", ["a", "b", "", ""], [1], 10, "double"), True, 5000, 1500),
    (QuizQuestion("Q?", ["a", "b", "", ""], [1], 10, "none"), True, 0, 0),
])
def test_points_follow_the_formula(question: QuizQuestion, correct: bool, elapsed: int, expected: int) -> None:
    assert points(question, correct, elapsed) == expected


def test_standings_streaks_and_ties() -> None:
    qs = {"q1": Q20, "q2": QuizQuestion("Q?", ["a", "b", "", ""], [1], 10, "double"), "q3": Q20}
    g = Game("lobby-1", "lobby", 1)
    for n, pid in enumerate(("pa", "pb", "pc", "pd")):
        g.apply({"op": "join", "player_id": pid, "name": pid.upper(), "secret": "s", "at": T0 + n})
    for n, item in enumerate(qs):
        g.apply({"op": "phase", "phase": "question", "item_id": item, "deadline_ms": T0 + 99_999, "at": T0 + 100 * (n + 1)})
        g.apply({"op": "phase", "phase": "reveal", "item_id": item, "at": T0 + 100 * (n + 1) + 50})

    def ans(pid: str, item: str, choice: int, ms: int) -> None:
        g.apply({"op": "answer", "player_id": pid, "item_id": item, "choice": choice, "elapsed_ms": ms, "at": T0})

    # A and B: the same points on every question (975 twice), but B was slower in total → A ranks first.
    ans("pa", "q1", 2, 1000)
    ans("pb", "q1", 2, 1010)
    ans("pa", "q2", 1, 0)
    ans("pb", "q2", 1, 0)
    ans("pa", "q3", 1, 0)  # wrong: A's streak resets
    ans("pb", "q3", 1, 0)
    # C and D: identical everything → join order decides.
    ans("pc", "q1", 1, 0)
    ans("pd", "q1", 1, 0)
    rows = {s.player_id: s for s in g.standings(qs)}
    assert rows["pa"].score == rows["pb"].score == 975 + 2000
    assert [s.player_id for s in g.standings(qs)] == ["pa", "pb", "pc", "pd"]
    assert rows["pa"].streak == 0 and rows["pa"].correct == 2 and rows["pa"].last_correct is False
    assert rows["pc"].score == 0 and rows["pc"].last_correct is None  # did not answer the last question
    g.apply({"op": "kick", "player_id": "pa", "at": T0})
    assert [s.player_id for s in g.standings(qs)] == ["pb", "pc", "pd"] and g.standings(qs)[0].rank == 1


def test_all_answered_needs_every_active_player_and_never_fires_with_none() -> None:
    g = Game("l-1", "l", 1)
    assert g.all_answered("q1") is False  # no players at all
    g.apply({"op": "join", "player_id": "pa", "name": "A", "secret": "s", "at": T0})
    g.apply({"op": "join", "player_id": "pb", "name": "B", "secret": "s", "at": T0})
    g.apply({"op": "phase", "phase": "question", "item_id": "q1", "deadline_ms": T0 + 99_999, "at": T0})
    assert g.all_answered("q1") is False  # nobody has answered yet
    g.apply({"op": "answer", "player_id": "pa", "item_id": "q1", "choice": 1, "elapsed_ms": 0, "at": T0})
    assert g.all_answered("q1") is False  # pb hasn't
    g.apply({"op": "kick", "player_id": "pb", "at": T0})
    assert g.all_answered("q1") is True  # the only remaining active player has answered
    g.apply({"op": "kick", "player_id": "pa", "at": T0})
    assert g.all_answered("q1") is False  # 0 active players: never true


def test_a_streak_counts_consecutive_correct_answers_and_scores_nothing() -> None:
    qs = {f"q{i}": Q20 for i in range(3)}
    g = Game("l-1", "l", 1)
    g.apply({"op": "join", "player_id": "p", "name": "P", "secret": "s", "at": T0})
    for i, item in enumerate(qs):
        g.apply({"op": "phase", "phase": "question", "item_id": item, "deadline_ms": T0 + 99_999, "at": T0 + i})
        g.apply({"op": "answer", "player_id": "p", "item_id": item, "choice": 2, "elapsed_ms": 10_000, "at": T0})
        g.apply({"op": "phase", "phase": "reveal", "item_id": item, "at": T0 + i})
    s = g.standings(qs)[0]
    assert s.streak == 3 and s.score == 3 * 750  # no streak bonus: Kahoot retired it


# ---- players ----

def test_nicknames_are_cleaned_and_deduplicated(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    a, b, c = at_lobby_with(hub, quiz, "  Ana  ", "ana", "Ana")
    assert (a.name, b.name, c.name) == ("Ana", "ana (2)", "Ana (3)")
    assert clean_name("x" * 50) == "x" * 20
    with pytest.raises(QuizError) as err:
        quiz.join("   ")
    assert err.value.code == "bad_nickname"
    assert state(hub)["player_count"] == 3 and [p["name"] for p in state(hub)["players"]] == ["Ana", "ana (2)", "Ana (3)"]


def test_join_resume_and_kick(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    assert quiz.join("Early").state == "no_game"  # a slide is on stage
    (a,) = at_lobby_with(hub, quiz, "Ana")
    assert a.state == "joined" and a.game_id == "qz-lobby-1" and a.secret
    assert quiz.join("Ana", key="phone-1").name == "Ana (2)"
    again = quiz.join("Ana", key="phone-1")  # a retried join: the same player, not a third one
    assert again.name == "Ana (2)" and state(hub)["player_count"] == 2
    assert quiz.join("Bo", game_id="other-1").state == "no_game"

    r = quiz.resume(a.player_id, a.secret)
    assert (r.state, r.player_id, r.name) == ("resumed", a.player_id, "Ana")
    assert quiz.resume(a.player_id, "wrong").state == "unknown_player"
    assert quiz.resume("p-nobody", "x").state == "unknown_player"

    run_action(hub, "quiz_kick", a.player_id)
    assert quiz.resume(a.player_id, a.secret).state == "kicked"
    assert [p["name"] for p in state(hub)["players"]] == ["Ana (2)"]
    assert quiz.join("Ana").name == "Ana"  # the name is free again
    with pytest.raises(QuizError):
        quiz.kick(a.player_id)
    goto_id(hub, "qz-podium")
    assert quiz.join("Late").state == "closed"


# ---- answers ----

def test_answers_every_result_state(rig: tuple[LiveHub, QuizService], clock: Clock) -> None:
    hub, quiz = rig
    a, b, kicked = at_lobby_with(hub, quiz, "Ana", "Bo", "Cy")
    quiz.kick(kicked.player_id)
    assert quiz.answer(a.player_id, a.secret, "qz-1", 2).state == "not_open"  # still the lobby
    hub.next()  # → question 1, open
    assert state(hub)["phase"] == "question"
    clock.t += 4000
    first = quiz.answer(a.player_id, a.secret, "qz-1", 2, elapsed_ms=3000)
    assert (first.state, first.choice, first.elapsed_ms) == ("accepted", 2, 3000)
    assert quiz.answer(a.player_id, a.secret, "qz-1", "2", elapsed_ms=3500).as_dict() == first.as_dict()  # retry
    assert quiz.answer(a.player_id, a.secret, "qz-1", 3).state == "duplicate"
    assert quiz.answer(a.player_id, "bad", "qz-1", 2).state == "unknown_player"
    assert quiz.answer("p-nobody", "x", "qz-1", 2).state == "unknown_player"
    assert quiz.answer(kicked.player_id, kicked.secret, "qz-1", 2).state == "kicked"
    assert quiz.answer(b.player_id, b.secret, "qz-2", 1).state == "not_open"  # not asked yet
    with pytest.raises(QuizError) as err:
        quiz.answer(b.player_id, b.secret, "qz-1", 5)
    assert err.value.code == "bad_choice"
    # A phone cannot have waited longer than the server has been asking (4 s so far).
    late = quiz.answer(b.player_id, b.secret, "qz-1", 1, elapsed_ms=60_000)
    assert (late.state, late.elapsed_ms) == ("accepted", 4000)
    assert state(hub)["answered_count"] == 2

    hub.next()  # → reveal: locked
    c = quiz.join("Di")
    assert quiz.answer(c.player_id, c.secret, "qz-1", 2).state == "too_late"
    assert quiz.answer(a.player_id, a.secret, "qz-1", 2).state == "accepted"  # a retried ack stays an ack


def test_an_answer_after_the_deadline_and_its_grace_is_too_late(rig: tuple[LiveHub, QuizService], clock: Clock) -> None:
    hub, quiz = rig
    a, b = at_lobby_with(hub, quiz, "Ana", "Bo")
    hub.next()
    clock.t += 20_000 + GRACE_MS  # the deadline plus the grace: still counts
    assert quiz.answer(a.player_id, a.secret, "qz-1", 2).state == "accepted"
    clock.t += 1
    assert quiz.answer(b.player_id, b.secret, "qz-1", 2).state == "too_late"
    # The chat fallback path: no secret, the server's own elapsed time.
    hub.next(), hub.next(), hub.next()  # reveal, leaderboard, question 2
    ok = quiz.answer_trusted(b.player_id, "qz-2", "3")
    assert (ok.state, ok.elapsed_ms) == ("accepted", 0)


def test_the_correct_answer_is_absent_until_the_reveal(rig: tuple[LiveHub, QuizService], clock: Clock) -> None:
    hub, quiz = rig
    (a,) = at_lobby_with(hub, quiz, "Ana")
    hub.next()
    clock.t += 2000
    quiz.answer(a.player_id, a.secret, "qz-1", 2, elapsed_ms=1000)
    snap, mine = state(hub), quiz.player_view(a.player_id, a.secret)
    for view in (snap, mine):
        assert "correct" not in json.dumps(view) and "distribution" not in view and "leaderboard" not in view
        assert "score" not in view
    assert snap["deadline_ms"] == T0 + 20_000 and snap["question_index"] == 0 and snap["question_count"] == 3
    assert mine["tiles"] == [1, 2, 3, 4] and mine["answer"] == {"choice": 2, "elapsed_ms": 1000}
    assert mine["question"] == "Which planet is the red one?" and mine["answers"] == ["Venus", "Mars", "Jupiter", "Saturn"]
    assert quiz.player_view(a.player_id, "wrong") is None

    hub.next()  # reveal
    snap, mine = state(hub), quiz.player_view(a.player_id, a.secret)
    assert snap["phase"] == "reveal" and snap["correct"] == [2] and snap["distribution"] == [0, 1, 0, 0]
    assert "leaderboard" not in snap
    assert mine["answer"]["correct"] is True and mine["correct"] == [2] and mine["score"] == 975 and mine["rank"] == 1
    hub.next()  # leaderboard
    board = state(hub)["leaderboard"]
    assert board == [{"id": a.player_id, "name": "Ana", "score": 975, "rank": 1, "correct": 1, "streak": 1,
                      "last_points": 975, "last_correct": True}]


# ---- phases and next ----

def test_next_steps_the_phases_of_a_question_before_moving_on(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    goto_id(hub, "qz-lobby")
    assert state(hub)["phase"] == "lobby" and state(hub)["question_index"] is None
    steps = []
    for _ in range(9):
        hub.next()
        steps.append((hub.current()["id"], state(hub)["phase"]))
    assert steps == [
        ("qz-1", "question"), ("qz-1", "reveal"), ("qz-1", "leaderboard"),
        ("qz-2", "question"), ("qz-2", "reveal"), ("qz-2", "leaderboard"),
        ("qz-3", "question"), ("qz-3", "reveal"), ("qz-3", "leaderboard"),
    ]
    hub.next()
    assert hub.current()["id"] == "qz-podium" and state(hub)["phase"] == "podium" and "leaderboard" in state(hub)
    hub.next()  # a quiz item outside any game is a plain item: next moves straight on
    assert hub.current()["id"] == "qz-orphan" and state(hub) is None
    hub.next()
    assert hub.current()["id"] != "qz-orphan"
    events = [e for e in read_jsonl(hub.folder / "live" / "events.jsonl") if e["event"] == "quiz_phase"]
    assert [e["phase"] for e in events][:4] == ["lobby", "question", "reveal", "leaderboard"]


def test_prev_is_never_taken_and_a_question_left_open_is_locked(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    (a,) = at_lobby_with(hub, quiz, "Ana")
    hub.next(), hub.next(), hub.next()  # question 1 → reveal → leaderboard
    hub.next()  # question 2, open
    hub.prev()  # back to question 1 at once: question 2 is locked
    assert hub.current()["id"] == "qz-1" and state(hub)["phase"] == "leaderboard"
    hub.next()
    assert hub.current()["id"] == "qz-2" and state(hub)["phase"] == "reveal"  # no second chance
    assert quiz.answer(a.player_id, a.secret, "qz-2", 1).state == "too_late"
    hub.prev(), hub.prev()
    assert hub.current()["id"] == "qz-lobby" and state(hub)["phase"] == "lobby"
    assert state(hub)["game_id"] == "qz-lobby-1" and state(hub)["player_count"] == 1  # the same game, resumed


def test_next_and_prev_on_every_other_item_are_unchanged(rig: tuple[LiveHub, QuizService]) -> None:
    hub, _ = rig
    last = len(hub.items) - 1
    for it in hub.items:
        for move, expected in (("next", min(it["index"] + 1, last)), ("prev", max(it["index"] - 1, 0))):
            hub.goto(0 if it["index"] else 1)
            hub.goto(it["index"])
            if move == "next" and it["type"] == "quiz" and it["id"] != "qz-orphan":
                continue  # a quiz question's own next: the test above
            run_action(hub, move)
            assert hub.index == expected, (it["id"], move)


def podium_steps(hub: LiveHub) -> tuple[str, Optional[int], Optional[int]]:
    q = state(hub) or {}
    return hub.current()["id"], q.get("podium_step"), q.get("podium_places")


def test_the_podium_reveals_3rd_2nd_and_1st_one_next_at_a_time(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    at_lobby_with(hub, quiz, "Ana", "Bo", "Cy", "Di")
    goto_id(hub, "qz-podium")
    assert podium_steps(hub) == ("qz-podium", 0, 3)  # nothing shown yet
    seen = []
    for _ in range(4):
        run_action(hub, "next")
        seen.append(podium_steps(hub))
    # 3rd, 2nd, 1st — then the 4th next moves on (qz-orphan is outside any game)
    assert seen == [("qz-podium", 1, 3), ("qz-podium", 2, 3), ("qz-podium", 3, 3), ("qz-orphan", None, None)]
    run_action(hub, "prev")  # back from the next item: the podium as it was left, every place shown
    assert podium_steps(hub) == ("qz-podium", 3, 3)
    for step in (2, 1, 0):  # prev hides one place at a time …
        run_action(hub, "prev")
        assert podium_steps(hub) == ("qz-podium", step, 3)
    run_action(hub, "prev")  # … and with none shown goes to the previous item
    assert hub.current()["id"] == "qz-3"
    run_action(hub, "next"), run_action(hub, "next"), run_action(hub, "next")  # reveal, leaderboard, podium
    assert podium_steps(hub) == ("qz-podium", 0, 3)  # entered afresh from the game: nothing shown
    records = read_jsonl(hub.folder / "live" / QUIZ_FILE)
    assert [r["step"] for r in records if r["op"] == "podium"] == [1, 2, 3, 2, 1, 0]


@pytest.mark.parametrize(("players", "places"), [(0, 0), (1, 1), (2, 2), (3, 3), (5, 3)])
def test_the_podium_steps_only_the_places_that_exist(rig: tuple[LiveHub, QuizService], players: int,
                                                     places: int) -> None:
    hub, quiz = rig
    at_lobby_with(hub, quiz, *[f"P{n}" for n in range(players)])
    goto_id(hub, "qz-podium")
    for step in range(1, places + 1):
        hub.next()
        assert podium_steps(hub) == ("qz-podium", step, places)
    hub.next()
    assert hub.current()["id"] == "qz-orphan"


def test_a_player_removed_on_the_podium_never_leaves_a_step_beyond_the_places(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    a, b, _ = at_lobby_with(hub, quiz, "Ana", "Bo", "Cy")
    goto_id(hub, "qz-podium")
    hub.next(), hub.next(), hub.next()
    quiz.kick(a.player_id)
    assert podium_steps(hub) == ("qz-podium", 2, 2)
    hub.prev()
    assert podium_steps(hub) == ("qz-podium", 1, 2)
    quiz.kick(b.player_id)
    hub.next()  # the only place left is shown already: on to the next item
    assert hub.current()["id"] == "qz-orphan"


def ranked_game(hub: LiveHub, quiz: QuizService, clock: Clock, players: int) -> list[Any]:
    """``players`` who all answer question 1 right, each a second slower than the one before:
    the join order is the final ranking (1st, 2nd, …)."""
    joined = at_lobby_with(hub, quiz, *[f"P{n}" for n in range(1, players + 1)])
    hub.next()  # question 1
    for n, p in enumerate(joined):
        clock.t = T0 + 1000 * (n + 1)
        assert quiz.answer(p.player_id, p.secret, "qz-1", 2).state == "accepted"
    return joined


def ranks(quiz: QuizService, joined: list[Any]) -> list[Optional[int]]:
    """Each player's rank as their phone gets it (``None`` when the view carries none)."""
    return [quiz.player_view(p.player_id, p.secret).get("rank") for p in joined]


def test_a_phone_learns_its_podium_place_only_once_the_stage_reveals_it(rig: tuple[LiveHub, QuizService],
                                                                        clock: Clock) -> None:
    """#147: the podium reveals 3rd, 2nd, 1st one Next at a time; each phone gets its own place
    only at that step — 4th and below once every place is shown — and Prev hides it again."""
    hub, quiz = rig
    joined = ranked_game(hub, quiz, clock, 5)
    goto_id(hub, "qz-podium")
    views = [quiz.player_view(p.player_id, p.secret) for p in joined]
    assert all("rank" not in v and v["rank_pending"] is True for v in views)  # step 0: nobody knows
    expected = {
        1: [None, None, 3, None, None],  # 3rd
        2: [None, 2, 3, None, None],  # 2nd
        3: [1, 2, 3, 4, 5],  # 1st: the podium is complete, so everyone else learns theirs
    }
    for step in (1, 2, 3):
        run_action(hub, "next")
        assert state(hub)["podium_step"] == step
        assert ranks(quiz, joined) == expected[step], step
    for step in (2, 1, 0):  # Prev hides a place again on the phone
        run_action(hub, "prev")
        assert ranks(quiz, joined) == expected.get(step, [None] * 5), step
    assert quiz.player_view(joined[0].player_id, joined[0].secret)["rank_pending"] is True


def test_the_last_question_gives_no_final_rank_before_the_podium(rig: tuple[LiveHub, QuizService],
                                                                  clock: Clock) -> None:
    """#147: the reveal and leaderboard of the last question would tell a phone its final place;
    they carry none (an earlier question still does)."""
    hub, quiz = rig
    joined = ranked_game(hub, quiz, clock, 2)
    hub.next()  # question 1's reveal: not the last question, the rank is shown as before
    assert ranks(quiz, joined) == [1, 2]
    goto_id(hub, "qz-3")  # the last question
    hub.next()  # its reveal
    assert ranks(quiz, joined) == [None, None]
    hub.next()  # its leaderboard
    views = [quiz.player_view(p.player_id, p.secret) for p in joined]
    assert state(hub)["phase"] == "leaderboard" and [v.get("rank") for v in views] == [None, None]
    assert all(v["rank_pending"] is True and v["score"] > 0 for v in views)


def test_lock_and_time_up_reveal(rig: tuple[LiveHub, QuizService], clock: Clock) -> None:
    hub, quiz = rig
    goto_id(hub, "qz-lobby")
    with pytest.raises(LiveError):
        run_action(hub, "quiz_lock")  # nothing open on the lobby
    hub.next()
    run_action(hub, "quiz_lock")
    assert state(hub)["phase"] == "reveal" and hub.current()["id"] == "qz-1"
    hub.next(), hub.next()  # leaderboard → question 2
    quiz.time_up("qz-lobby-1", "qz-1")  # a stale timer: ignored
    assert state(hub)["phase"] == "question"
    quiz.time_up("qz-lobby-1", "qz-2")
    assert state(hub)["phase"] == "reveal"


# ---- closing early when everyone has answered (#105) ----

def test_closes_early_once_every_active_player_has_answered(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)  # keep the test fast; the grace itself is unit-tested
    sid, _ = quiz_session

    async def run() -> str:
        hub, quiz = await bound_rig(sid, clock)
        a, b = at_lobby_with(hub, quiz, "Ana", "Bo")
        hub.next()  # question 1, open
        quiz.answer(a.player_id, a.secret, "qz-1", 2)
        assert not await wait_for_phase(hub, "reveal", 0.2)  # one of two answered: still open
        quiz.answer(b.player_id, b.secret, "qz-1", 1)  # the last active player answers
        await wait_for_phase(hub, "reveal", 2.0)
        return state(hub)["phase"]

    assert asyncio.run(run()) == "reveal"


def test_the_option_off_leaves_the_question_open_for_everyone_answering(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)
    sid, folder = quiz_session
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    lobby = next(it for sec in raw["sections"] for it in sec["items"] if it.get("id") == "qz-lobby")
    lobby.setdefault("options", {})["close_when_all_answered"] = False
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")

    async def run() -> str:
        hub, quiz = await bound_rig(sid, clock)
        a, b = at_lobby_with(hub, quiz, "Ana", "Bo")
        hub.next()
        quiz.answer(a.player_id, a.secret, "qz-1", 2)
        quiz.answer(b.player_id, b.secret, "qz-1", 1)
        assert not await wait_for_phase(hub, "reveal", 0.3)  # well past the (patched) grace: still open
        return state(hub)["phase"]

    assert asyncio.run(run()) == "question"


def test_a_kicked_hold_out_lets_the_rest_close_early(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)
    sid, _ = quiz_session

    async def run() -> str:
        hub, quiz = await bound_rig(sid, clock)
        a, b, c = at_lobby_with(hub, quiz, "Ana", "Bo", "Cy")
        hub.next()
        quiz.answer(a.player_id, a.secret, "qz-1", 2)
        quiz.answer(b.player_id, b.secret, "qz-1", 1)  # Cy never answers
        assert not await wait_for_phase(hub, "reveal", 0.2)
        quiz.kick(c.player_id)  # the only hold-out removed: everyone left has answered
        await wait_for_phase(hub, "reveal", 2.0)
        return state(hub)["phase"]

    assert asyncio.run(run()) == "reveal"


def test_a_chat_player_counts_towards_closing_early(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)
    sid, _ = quiz_session

    async def run() -> str:
        hub, quiz = await bound_rig(sid, clock)
        goto_id(hub, "qz-lobby")
        a = quiz.join("Ana")
        chatter = quiz.join("Zed", source="chat")
        hub.next()
        quiz.answer(a.player_id, a.secret, "qz-1", 2)
        quiz.answer_trusted(chatter.player_id, "qz-1", 1)  # the chat fallback: no secret
        await wait_for_phase(hub, "reveal", 2.0)
        return state(hub)["phase"]

    assert asyncio.run(run()) == "reveal"


def test_no_players_never_closes_early(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)
    sid, _ = quiz_session

    async def run() -> str:
        hub, quiz = await bound_rig(sid, clock)
        goto_id(hub, "qz-lobby")
        hub.next()  # question 1, open — nobody ever joined
        assert not await wait_for_phase(hub, "reveal", 0.3)
        return state(hub)["phase"]

    assert asyncio.run(run()) == "question"


def test_replaying_after_a_restart_keeps_an_early_close(
    quiz_session: tuple[str, Path], clock: Clock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.quiz.service.EARLY_CLOSE_MS", 50)
    sid, _ = quiz_session

    async def run() -> None:
        hub, quiz = await bound_rig(sid, clock)
        a, b = at_lobby_with(hub, quiz, "Ana", "Bo")
        hub.next()
        quiz.answer(a.player_id, a.secret, "qz-1", 2)
        quiz.answer(b.player_id, b.secret, "qz-1", 1)
        assert await wait_for_phase(hub, "reveal", 2.0)

    asyncio.run(run())

    hub2, quiz2 = make_rig(sid, clock)  # a fresh server on the same session folder
    assert state(hub2)["phase"] == "reveal" and state(hub2)["correct"] == [2]
    late = quiz2.join("Cy")  # joins after the early close: never got a chance to answer
    assert quiz2.answer(late.player_id, late.secret, "qz-1", 2).state == "too_late"  # exactly as today


def test_a_new_game_and_a_session_reset(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    at_lobby_with(hub, quiz, "Ana")
    run_action(hub, "quiz_new_game")
    assert state(hub)["game_id"] == "qz-lobby-2" and state(hub)["player_count"] == 0
    hub.next()
    with pytest.raises(LiveError):
        run_action(hub, "quiz_new_game")  # only from the lobby
    hub.reset()
    assert quiz.games == {} and quiz.join("Ana").state == "no_game"
    goto_id(hub, "qz-lobby")
    assert state(hub)["game_id"] == "qz-lobby-1" and state(hub)["player_count"] == 0


# ---- durability ----

def test_a_restart_replays_quiz_jsonl_to_the_same_scores_and_phase(quiz_session: tuple[str, Path], clock: Clock) -> None:
    sid, folder = quiz_session
    hub, quiz = make_rig(sid, clock)
    a, b, c = at_lobby_with(hub, quiz, "Ana", "Bo", "Cy")
    quiz.kick(c.player_id)
    hub.next()  # question 1
    clock.t += 2000
    quiz.answer(a.player_id, a.secret, "qz-1", 2, elapsed_ms=1500)
    quiz.answer(b.player_id, b.secret, "qz-1", 1, elapsed_ms=900)
    hub.next(), hub.next(), hub.next()  # reveal, leaderboard, question 2 (open)
    clock.t += 3000
    quiz.answer(b.player_id, b.secret, "qz-2", 3, elapsed_ms=2500)
    before = state(hub)
    game = quiz.games["qz-lobby-1"]
    scores = [s.as_dict() for s in game.standings(quiz.scopes()["qz-lobby"].questions)]

    hub2, quiz2 = make_rig(sid, clock)  # a fresh server on the same session folder
    assert hub2.current()["id"] == "qz-2"
    assert state(hub2) == before and state(hub2)["phase"] == "question"
    assert [s.as_dict() for s in quiz2.games["qz-lobby-1"].standings(quiz2.scopes()["qz-lobby"].questions)] == scores
    assert quiz2.resume(a.player_id, a.secret).state == "resumed"
    assert quiz2.resume(c.player_id, c.secret).state == "kicked"
    assert quiz2.answer(b.player_id, b.secret, "qz-2", 1).state == "duplicate"
    # Question 2 opened 3 s ago: Ana's reported 4 s is clamped to 3 s.
    assert quiz2.answer(a.player_id, a.secret, "qz-2", 1, elapsed_ms=4000).elapsed_ms == 3000
    hub2.next(), hub2.next()
    board = {r["name"]: r for r in state(hub2)["leaderboard"]}
    # Ana: 1000 × (1 − 1.5/20/2) = 962.5 → 963, then double: 2000 × (1 − 3/10/2) = 1700. Bo: wrong, then 1750.
    assert board["Ana"]["score"] == 963 + 1700 and board["Bo"]["score"] == 1750
    assert board["Bo"]["rank"] == 2 and board["Ana"]["streak"] == 2 and board["Bo"]["streak"] == 1


def test_a_restart_in_the_middle_of_the_podium_keeps_its_step(quiz_session: tuple[str, Path], clock: Clock) -> None:
    sid, _ = quiz_session
    hub, quiz = make_rig(sid, clock)
    at_lobby_with(hub, quiz, "Ana", "Bo", "Cy")
    goto_id(hub, "qz-podium")
    hub.next(), hub.next()  # 3rd and 2nd shown
    hub2, _ = make_rig(sid, clock)  # a fresh server on the same session folder
    assert podium_steps(hub2) == ("qz-podium", 2, 3)
    hub2.next()
    assert podium_steps(hub2) == ("qz-podium", 3, 3)
    hub2.next()
    assert hub2.current()["id"] == "qz-orphan"


def test_a_question_whose_time_ran_out_during_the_restart_is_revealed(quiz_session: tuple[str, Path], clock: Clock) -> None:
    sid, _ = quiz_session
    hub, quiz = make_rig(sid, clock)
    (a,) = at_lobby_with(hub, quiz, "Ana")
    hub.next()
    quiz.answer(a.player_id, a.secret, "qz-1", 2, elapsed_ms=1000)
    clock.t += 60_000  # the server was down past the deadline
    hub2, _ = make_rig(sid, clock)
    assert state(hub2)["phase"] == "reveal" and state(hub2)["correct"] == [2]


def test_the_time_up_reveal_is_scheduled_on_the_loop_after_a_restart(quiz_session: tuple[str, Path], clock: Clock) -> None:
    sid, _ = quiz_session
    hub, _ = make_rig(sid, clock)
    goto_id(hub, "qz-lobby")
    hub.next()
    clock.t += 20_000 + GRACE_MS - 50  # 50 ms of the grace left when the server is back

    async def restart() -> str:
        hub2 = LiveHub(SessionStore(load_config()))
        QuizService(hub2, clock=clock)
        hub2.bind(asyncio.get_running_loop())
        hub2.activate(sid)
        assert state(hub2)["phase"] == "question"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0  # generous ceiling; the reveal is due ~50 ms out
        while state(hub2)["phase"] != "reveal" and loop.time() < deadline:
            await asyncio.sleep(0.01)
        return state(hub2)["phase"]

    assert asyncio.run(restart()) == "reveal"


def test_a_failed_write_is_kept_and_written_with_the_next_record(rig: tuple[LiveHub, QuizService]) -> None:
    hub, quiz = rig
    goto_id(hub, "qz-lobby")
    path = hub.folder / "live" / QUIZ_FILE
    path.rename(path.with_suffix(".bak"))
    path.mkdir()  # OneDrive holding it: the append fails
    a = quiz.join("Ana")
    assert a.state == "joined" and QUIZ_FILE in (hub.write_error or "")
    path.rmdir()
    path.with_suffix(".bak").rename(path)
    quiz.join("Bo")
    assert hub.write_error is None
    names = [r["name"] for r in read_jsonl(path) if r["op"] == "join"]
    assert names == ["Ana", "Bo"]
