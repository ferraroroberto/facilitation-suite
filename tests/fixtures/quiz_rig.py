"""A quiz game on a fake clock: the demo session with a quiz section, and the helpers every quiz test drives it with."""

from __future__ import annotations

from typing import Any

from src.config import load_config
from src.live.hub import LiveHub
from src.quiz.service import QuizService
from src.sessions.store import SessionStore

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
    def __init__(self, t: int = T0) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def make_rig(sid: str, clock: Clock) -> tuple[LiveHub, QuizService]:
    hub = LiveHub(SessionStore(load_config()))
    quiz = QuizService(hub, clock=clock)
    hub.activate(sid)
    return hub, quiz


def goto_id(hub: LiveHub, item_id: str) -> None:
    hub.goto(hub.item_by_id(item_id)["index"])


def state(hub: LiveHub) -> dict[str, Any]:
    return hub.snapshot()["state"]["quiz"]


def at_lobby_with(hub: LiveHub, quiz: QuizService, *names: str) -> list[Any]:
    goto_id(hub, "qz-lobby")
    return [quiz.join(n) for n in names]
