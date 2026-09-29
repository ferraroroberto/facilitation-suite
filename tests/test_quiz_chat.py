"""The quiz's Zoom-chat fallback and host controls (#54): the parser, chat players, accept_chat,
the rehearsal switch, Space on a question, and a whole game rehearsed through the simulator's script."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.chat.hub import ChatHub
from src.chat.reader import SIM_PEOPLE, load_script
from src.config import load_config
from src.live.actions import catalog, run_action
from src.live.capture import CaptureService
from src.live.hub import LiveError, LiveHub
from src.quiz.chat import ChatAnswers, parse_choice
from src.quiz.service import QuizService
from src.sessions.store import SESSION_FILE, SessionStore
from tests.fixtures.demo import build_demo_session
from tests.test_quiz_engine import QUIZ_SECTION, T0, Clock, goto_id, state


class Rig:
    def __init__(self, sid: str, clock: Clock) -> None:
        self.clock = clock
        self.hub = LiveHub(SessionStore(load_config()))
        self.chat = ChatHub(self.hub)
        self.capture = CaptureService(self.hub, self.chat)
        self.quiz = QuizService(self.hub, clock=clock)
        self.answers = ChatAnswers(self.quiz, self.chat, count_own=lambda: self.capture.count_own)
        self.hub.activate(sid)

    def say(self, *rows: tuple[str, str], after_ms: int = 0) -> None:
        """Chat messages from (sender, text), received ``after_ms`` from now on the fake clock."""
        self.clock.t += after_ms
        self.chat.ingest(uuid.uuid4().hex, [{"sender": s, "text": t, "time": "10:00", "received_at": self.clock.t}
                                            for s, t in rows], baseline=False, source="zoom")

    def players(self) -> dict[str, dict[str, Any]]:
        game = self.quiz.games[self.quiz.latest["qz-lobby"]]
        return {p.name: {"id": p.id, "source": p.source, "kicked": p.kicked} for p in game.players.values()}

    def answer(self, name: str, item_id: str) -> Any:  # noqa: ANN401 — the engine's Answer, or None
        game = self.quiz.games[self.quiz.latest["qz-lobby"]]
        pid = self.players()[name]["id"]
        return game.answers.get((pid, item_id))


def _session(env: Path, *, accept_chat: bool | None = None) -> str:
    sid, folder = build_demo_session(env / "sessions" / "demo" / "quiz", env / "sessions.local.yaml")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    section = yaml.safe_load(yaml.safe_dump(QUIZ_SECTION))  # a deep copy
    if accept_chat is not None:
        section["items"][0]["options"]["accept_chat"] = accept_chat
    raw["sections"].insert(1, section)
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return sid


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rig(isolated_env: Path, clock: Clock) -> Rig:
    return Rig(_session(isolated_env), clock)


# ---- the parser ----

@pytest.mark.parametrize(("text", "choice"), [
    ("A", 1), ("b", 2), ("C", 3), ("d", 4), ("1", 1), ("4", 4),
    ("  B  ", 2), ("b.", 2), ("C!", 3), ("d?!", 4), ("2)", 2), ("a,", 1), ("3…", 3),
])
def test_a_single_letter_or_number_is_an_answer(text: str, choice: int) -> None:
    assert parse_choice(text) == choice


@pytest.mark.parametrize("text", [
    "", "   ", "E", "e", "0", "5", "AB", "B C", "B is right", "answer B", ". B", "b 😀", "Bb", "12", None,
])
def test_anything_else_is_not_an_answer(text: Any) -> None:  # noqa: ANN401 — None too
    assert parse_choice(text) is None


# ---- chat answers during a question ----

def test_chat_b_scores_for_that_zoom_name_and_the_first_answer_wins(rig: Rig) -> None:
    goto_id(rig.hub, "qz-lobby")
    rig.say(("Ana Diaz", "B"))  # the lobby: not a question
    assert rig.players() == {}
    rig.hub.next()  # question 1 opens
    rig.say(("Ana Diaz", "b."), after_ms=2500)
    rig.say(("Ana Diaz", "C"), ("Bo Lee", "hello"), ("Bo Lee", "3"), after_ms=1000)
    players = rig.players()
    assert players["Ana Diaz"]["source"] == "chat" and set(players) == {"Ana Diaz", "Bo Lee"}
    first = rig.answer("Ana Diaz", "qz-1")
    assert (first.choice, first.elapsed_ms) == (2, 2500)  # the second message changed nothing
    assert (rig.answer("Bo Lee", "qz-1").choice, rig.answer("Bo Lee", "qz-1").elapsed_ms) == (3, 3500)
    assert state(rig.hub)["answered_count"] == 2

    rig.hub.next()  # reveal: closed, a late message is ignored
    rig.say(("Cy Park", "A"))
    assert "Cy Park" not in rig.players()
    assert state(rig.hub)["distribution"] == [0, 1, 1, 0]
    rig.hub.next()  # leaderboard
    board = {r["name"]: r["score"] for r in state(rig.hub)["leaderboard"]}
    assert board["Ana Diaz"] > 0 and board["Bo Lee"] == 0  # Mars (2) is right

    rig.hub.next()  # question 2
    rig.say(("Ana Diaz", "1"), after_ms=1000)  # the same chat player again, no second join
    assert len(rig.players()) == 2 and rig.answer("Ana Diaz", "qz-2").choice == 1


def test_letters_beyond_the_questions_answers_are_ignored(rig: Rig) -> None:
    goto_id(rig.hub, "qz-3")  # "Is the Sun a star?": two answers
    rig.say(("Ana Diaz", "C"), ("Bo Lee", "4"), after_ms=500)
    assert rig.players() == {}
    rig.say(("Ana Diaz", "a"), after_ms=500)
    assert rig.answer("Ana Diaz", "qz-3").choice == 1


def test_a_message_from_before_the_question_opened_is_not_an_answer(rig: Rig, clock: Clock) -> None:
    goto_id(rig.hub, "qz-1")
    early = clock.t - 1
    rig.chat.ingest("b1", [{"sender": "Ana Diaz", "text": "B", "time": "10:00", "received_at": early}],
                    baseline=False, source="zoom")
    assert rig.players() == {}


def test_the_response_time_is_clamped_to_the_questions_window(rig: Rig, clock: Clock) -> None:
    goto_id(rig.hub, "qz-2")  # 10 s
    clock.t += 11_000  # past the deadline, inside the grace
    rig.chat.ingest("b2", [{"sender": "Ana Diaz", "text": "A", "time": "10:00", "received_at": clock.t + 5000}],
                    baseline=False, source="zoom")
    assert rig.answer("Ana Diaz", "qz-2").elapsed_ms == 10_000


def test_a_phone_player_and_a_chat_player_with_the_same_name_stay_distinct(rig: Rig) -> None:
    goto_id(rig.hub, "qz-lobby")
    phone = rig.quiz.join("Ana Diaz")
    rig.hub.next()
    rig.quiz.answer(phone.player_id, phone.secret, "qz-1", 2, elapsed_ms=900)
    rig.say(("Ana Diaz", "B"), after_ms=1500)
    players = rig.players()
    assert players["Ana Diaz"] == {"id": phone.player_id, "source": "phone", "kicked": False}
    assert players["Ana Diaz (2)"]["source"] == "chat"
    rig.hub.next(), rig.hub.next()  # reveal, leaderboard
    names = [r["name"] for r in state(rig.hub)["leaderboard"]]
    assert sorted(names) == ["Ana Diaz", "Ana Diaz (2)"]
    assert {p["name"]: p["source"] for p in state(rig.hub)["players"]} == {"Ana Diaz": "phone", "Ana Diaz (2)": "chat"}


def test_a_kicked_chat_player_stays_out(rig: Rig) -> None:
    goto_id(rig.hub, "qz-1")
    rig.say(("Ana Diaz", "B"), after_ms=500)
    run_action(rig.hub, "quiz_kick", rig.players()["Ana Diaz"]["id"])
    rig.hub.next(), rig.hub.next(), rig.hub.next()  # reveal, leaderboard, question 2
    rig.say(("Ana Diaz", "A"), after_ms=500)
    assert list(rig.players()) == ["Ana Diaz"] and rig.players()["Ana Diaz"]["kicked"]
    assert state(rig.hub)["answered_count"] == 0


# ---- the switches: accept_chat and the rehearsal ----

def test_chat_answers_count_only_while_the_lobby_accepts_them(isolated_env: Path, clock: Clock) -> None:
    rig = Rig(_session(isolated_env, accept_chat=False), clock)
    goto_id(rig.hub, "qz-1")
    assert state(rig.hub)["accept_chat"] is False
    rig.say(("Ana Diaz", "B"), after_ms=500)
    assert rig.players() == {}


def test_accept_chat_is_on_by_default(rig: Rig) -> None:
    goto_id(rig.hub, "qz-lobby")
    assert state(rig.hub)["accept_chat"] is True


def test_own_messages_count_only_in_the_rehearsal(rig: Rig) -> None:
    goto_id(rig.hub, "qz-1")
    rig.say(("You", "B"), after_ms=500)
    assert rig.players() == {}  # the facilitator's own message
    run_action(rig.hub, "capture_count_own")  # the presenter's rehearsal switch
    rig.say(("You", "B"), after_ms=500)
    assert rig.players()["You"]["source"] == "chat" and rig.answer("You", "qz-1").choice == 2


# ---- the host's controls ----

def test_space_locks_an_open_question_and_is_unchanged_elsewhere(rig: Rig) -> None:
    goto_id(rig.hub, "qz-1")
    assert state(rig.hub)["phase"] == "question"
    run_action(rig.hub, "space")
    assert state(rig.hub)["phase"] == "reveal"
    with pytest.raises(LiveError) as err:  # revealed: Space is back to capture/timer — a quiz item has neither
        run_action(rig.hub, "space")
    assert err.value.code == "nothing_to_start"
    goto_id(rig.hub, "qz-lobby")
    with pytest.raises(LiveError):
        run_action(rig.hub, "space")
    assert state(rig.hub)["phase"] == "lobby"


def test_the_quiz_actions_are_listed() -> None:
    ids = {a["id"]: a for a in catalog()}
    assert {"quiz_lock", "quiz_kick", "quiz_new_game", "next"} <= set(ids)
    assert ids["quiz_lock"]["stream_deck"] and ids["quiz_kick"]["arg"] == "id"


# ---- a whole game, rehearsed alone through the simulator's script ----

def test_a_three_question_game_rehearsed_through_the_simulator(rig: Rig, clock: Clock) -> None:
    """Lobby → three questions (question, reveal, leaderboard each — the last one's reveal goes straight
    on, #158) → podium, every answer from the
    simulator's own ``list`` script — what the presenter's "Simulate answers" sends on a quiz item."""
    goto_id(rig.hub, "qz-lobby")
    phases = [state(rig.hub)["phase"]]
    letters = {"qz-1": ["B", "b", "A.", "2", "C!", "B", "D", "b"], "qz-2": ["A", "c", "B", "1", "3", "a", "C", "A"],
               "qz-3": ["A", "B", "a", "1", "C", "b", "A", "a"]}
    for item_id, answers in letters.items():
        rig.hub.next()  # the question opens
        assert rig.hub.current()["id"] == item_id and state(rig.hub)["phase"] == "question"
        for msg in load_script("list:600", answers):
            rig.say((msg["sender"], msg["text"]), after_ms=int(msg["after"] * 1000))
        phases.append(state(rig.hub)["phase"])
        rig.hub.next()  # reveal
        phases.append(state(rig.hub)["phase"])
        rig.hub.next()  # leaderboard — after the last question, the podium
        phases.append(state(rig.hub)["phase"])
    snap = state(rig.hub)
    assert snap["phase"] == "podium" and rig.hub.current()["id"] == "qz-podium"
    assert phases == ["lobby"] + ["question", "reveal", "leaderboard"] * 2 + ["question", "reveal", "podium"]
    assert snap["player_count"] == 8 and {p["source"] for p in snap["players"]} == {"chat"}
    assert {p["name"] for p in snap["players"]} == set(SIM_PEOPLE[:8])
    game = rig.quiz.games["qz-lobby-1"]
    assert [game.distribution(i) for i in letters] == [[1, 5, 1, 1], [4, 1, 3, 0], [5, 2, 0, 0]]  # "C" on qz-3: ignored
    board = snap["leaderboard"]
    assert [r["rank"] for r in board] == list(range(1, 9)) and board[0]["score"] > 0
    assert clock.t > T0
