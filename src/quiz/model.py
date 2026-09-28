"""The quiz item format — the contract every later quiz step builds on (#50).

Three activity types, one plug-in folder each under ``app/activities/``:

- ``quiz_lobby`` (no capture) starts a game. Options: ``title`` (the quiz's
  name) and ``accept_chat`` (answers typed in the Zoom chat count too, Step 6).
  The game is the ``quiz`` items that follow it in plan order, up to the next
  ``quiz_podium``.
- ``quiz`` is one question: ``Item.question`` plus the options ``answer_1`` …
  ``answer_4`` (2–4 non-empty), ``correct`` (1-based and comma-separated, as
  Kahoot writes it: ``"2"`` or ``"1,3"``), ``time_limit`` (seconds, one of
  ``TIME_LIMITS``) and ``points`` (one of ``POINTS``).
- ``quiz_podium`` (no capture) ends the game.

``app/activities/quiz/editor.json`` offers the same choices; a unit test keeps
the two in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.errors import DomainError

TIME_LIMITS: tuple[int, ...] = (5, 10, 20, 30, 60, 90, 120, 240)
DEFAULT_TIME_LIMIT = 20
POINTS: tuple[str, ...] = ("standard", "double", "none")
DEFAULT_POINTS = "standard"
ANSWERS = 4  # answer slots per question
MIN_ANSWERS = 2


class QuizError(DomainError):
    """A quiz file or a quiz item that cannot be used."""


class InvalidQuestion(ValueError):
    """One question breaks the format: ``reason`` is a stable code, the message says what."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class QuizQuestion:
    """One question: ``answers`` always has ``ANSWERS`` slots (``""`` = unused), ``correct`` is 1-based."""

    question: str
    answers: list[str]
    correct: list[int]
    time_limit: int = DEFAULT_TIME_LIMIT
    points: str = DEFAULT_POINTS

    def options(self) -> dict[str, Any]:
        """The ``quiz`` item's options."""
        return {
            **{f"answer_{i + 1}": a for i, a in enumerate(self.answers)},
            "correct": ",".join(str(c) for c in self.correct),
            "time_limit": self.time_limit,
            "points": self.points,
        }


def cell_text(value: Any) -> str:
    """A spreadsheet cell or option as text: ``None`` → ``""``, ``3.0`` → ``"3"``."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_correct(value: Any) -> list[int]:
    """``1``, ``"2"`` or ``"1, 3"`` → sorted 1-based answer numbers."""
    text = cell_text(value)
    if not text:
        raise InvalidQuestion("no_correct", "no correct answer")
    out: set[int] = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= ANSWERS:
            raise InvalidQuestion("bad_correct", f"the correct answer {text!r} is not a list of answer numbers 1–{ANSWERS}")
        out.add(int(part))
    if not out:
        raise InvalidQuestion("no_correct", "no correct answer")
    return sorted(out)


def parse_time_limit(value: Any) -> int:
    """Seconds, one of ``TIME_LIMITS``; empty = ``DEFAULT_TIME_LIMIT``."""
    text = cell_text(value)
    if not text:
        return DEFAULT_TIME_LIMIT
    if not text.isdigit() or int(text) not in TIME_LIMITS:
        raise InvalidQuestion("bad_time_limit",
                              f"the time limit {text!r} is not one of {', '.join(map(str, TIME_LIMITS))} seconds")
    return int(text)


def make_question(question: Any, answers: list[Any], correct: Any, time_limit: Any = None,
                  points: Any = None) -> QuizQuestion:
    """A checked ``QuizQuestion`` from raw values (a spreadsheet row, an item's options).

    Raises ``InvalidQuestion`` for the first rule it breaks.
    """
    text = cell_text(question)
    if not text:
        raise InvalidQuestion("no_question", "no question text")
    slots = [cell_text(a) for a in answers[:ANSWERS]]
    slots += [""] * (ANSWERS - len(slots))
    if sum(1 for a in slots if a) < MIN_ANSWERS:
        raise InvalidQuestion("too_few_answers", f"a question needs at least {MIN_ANSWERS} answers")
    right = parse_correct(correct)
    empty = [c for c in right if not slots[c - 1]]
    if empty:
        raise InvalidQuestion("correct_empty_answer", f"the correct answer {empty[0]} points at an empty answer")
    pts = cell_text(points) or DEFAULT_POINTS
    if pts not in POINTS:
        raise InvalidQuestion("bad_points", f"points {pts!r} is not one of {', '.join(POINTS)}")
    return QuizQuestion(text, slots, right, parse_time_limit(time_limit), pts)


def question_from_item(item: Any) -> QuizQuestion:
    """A ``quiz`` plan item (``Item`` or its run dict) read back as a checked question."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    opts = get("options") or {}
    try:
        return make_question(get("question", ""), [opts.get(f"answer_{i + 1}") for i in range(ANSWERS)],
                             opts.get("correct"), opts.get("time_limit"), opts.get("points"))
    except InvalidQuestion as exc:
        raise QuizError(422, "quiz_item_invalid", f"Quiz question {get('id', '')}: {exc}",
                        {"item_id": get("id", ""), "reason": exc.reason}) from exc
