"""The quiz game (#51): players, phases, answers and scores — pure, no I/O, no clock.

**Event-sourced.** Every change to a game is one record (a plain dict with an
``op``), and ``Game.apply`` is the only thing that changes a game. The service
(``service.py``) applies each record and appends it to the session's
``live/quiz.jsonl``; a restarted server replays that file through the same
``apply``, so the game that was live and the game that is rebuilt cannot
differ. **Scores are never stored**: ``Game.standings`` derives them from the
answers and the questions as the plan has them now.

Records (every one also carries ``game`` and ``at``, epoch ms):

- ``{"op": "game", "lobby_id", "run"}`` — a game opens (``game_id`` =
  ``<lobby item id>-<run>``; a replay of the same quiz is run 2, 3, …).
- ``{"op": "join", "player_id", "name", "secret", "key", "source"}`` — ``key``
  is the caller's optional idempotency key (a retried join returns the same
  player); ``source`` is ``phone`` or ``chat`` (absent in older files: ``phone``).
- ``{"op": "resume", "player_id"}`` / ``{"op": "kick", "player_id"}``.
- ``{"op": "phase", "phase", "item_id"}`` — the stage shows ``phase`` of
  ``item_id``; entering a question for the first time also carries
  ``deadline_ms`` and opens it.
- ``{"op": "lock", "item_id"}`` — a question closes without the stage
  showing its reveal (the presenter moved away mid-question).
- ``{"op": "answer", "player_id", "item_id", "choice", "elapsed_ms", "client_ms"}``.

Phases: ``lobby → question → reveal → leaderboard → (next question) … →
podium``. A question is **open** from its ``question`` record until its
``reveal`` (time up, answers locked, or the presenter's ``next``) or a
``lock``; only an open question takes answers, and a question opens once.

Scoring follows Kahoot's published rule ("How points work",
https://support.kahoot.com/hc/en-us/articles/115002303908-How-points-work —
the page refuses scripted fetches, so it was checked through its search
snippet, 2026-09-28, and matches the formula in #34):

    points = round(points_possible × (1 − (response_time / time_limit) / 2))

with ``points_possible`` 1000 (``standard``), 2000 (``double``) or 0
(``none``), full points for an answer inside the first 0.5 s, and 0 for a
wrong or missing answer. A question with several correct answers takes one
choice, and any correct one scores (Kahoot's single-select quiz).

**Streaks:** Kahoot counts answer streaks but, since it retired the streak
bonus, awards **no points** for them (same help-centre article; Kahoot's
"Streak vs Progress" page says the same). So ``streak`` here is the number
of consecutive closed questions a player answered correctly — shown, never
scored. A wrong or missing answer resets it; a ``none``-points question
counts like any other.

**Ties** on score go to the smaller total response time over the player's
correct answers, then to whoever joined first — so ranks never tie.

Only **closed** questions count towards scores, streaks and the
distribution: while a question is open nothing derived from its answers
(and never its correct answer) is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.quiz.model import ANSWERS, QuizError, QuizQuestion

PHASES = ("lobby", "question", "reveal", "leaderboard", "podium")
POSSIBLE = {"standard": 1000, "double": 2000, "none": 0}
FULL_POINTS_MS = 500  # Kahoot: an answer inside the first half second scores the full points
GRACE_MS = 1500  # an answer sent before 00:00 still counts if it reaches the server this late
MAX_NAME = 20  # characters in a nickname (a suffix " (2)" may add to it)

# Answer results — each a distinct state the phone (or the chat fallback) can say something different for.
ACCEPTED = "accepted"  # locked in (a retry of the same answer gets the same ack)
DUPLICATE = "duplicate"  # this player already answered this question differently; the first answer stands
TOO_LATE = "too_late"  # the question closed (time up, locked or moved on) before the answer arrived
NOT_OPEN = "not_open"  # the question has not been asked yet (or is not a question of this game)
UNKNOWN_PLAYER = "unknown_player"  # no such player in this game, or the secret does not match
KICKED = "kicked"  # the host removed this player

# Where a player plays from.
PHONE = "phone"  # the public player page
CHAT = "chat"  # the Zoom-chat fallback (Step 6)

# Join and resume results.
JOINED = "joined"
RESUMED = "resumed"
NO_GAME = "no_game"  # no quiz on stage now (or the named game is not the one on stage)
CLOSED = "closed"  # the game reached its podium


def points(question: QuizQuestion, correct: bool, elapsed_ms: int) -> int:
    """One answer's points (see the module docstring for the rule and its source)."""
    possible = POSSIBLE.get(question.points, POSSIBLE["standard"])
    if not correct or possible == 0:
        return 0
    if elapsed_ms < FULL_POINTS_MS:
        return possible
    limit_ms = question.time_limit * 1000
    t = max(0, min(elapsed_ms, limit_ms))
    return int(possible * (1 - (t / limit_ms) / 2) + 0.5)  # round half up, as Kahoot rounds


def clean_name(nickname: Any) -> str:
    """A nickname on one line, at most ``MAX_NAME`` characters; empty is refused."""
    name = " ".join(str(nickname or "").split())[:MAX_NAME].strip()
    if not name:
        raise QuizError(422, "bad_nickname", "A nickname is needed to join")
    return name


@dataclass
class Player:
    id: str
    name: str
    secret: str
    seq: int  # join order: the last tie-break
    key: Optional[str] = None
    kicked: bool = False
    source: str = PHONE


@dataclass
class Answer:
    player_id: str
    item_id: str
    choice: int  # 1-based
    elapsed_ms: int  # the response time scored: client-reported, clamped to the server's window
    at: int


@dataclass
class QuestionRun:
    item_id: str
    opened_at: int
    deadline_ms: int
    closed_at: Optional[int] = None
    shown: str = "question"  # the last phase the stage showed of it

    @property
    def open(self) -> bool:
        return self.closed_at is None


@dataclass
class AnswerResult:
    state: str
    item_id: str
    choice: Optional[int] = None
    elapsed_ms: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "item_id": self.item_id, "choice": self.choice, "elapsed_ms": self.elapsed_ms}


@dataclass
class Standing:
    player_id: str
    name: str
    score: int = 0
    correct: int = 0
    streak: int = 0
    correct_ms: int = 0  # total response time over correct answers: the tie-break
    last_points: int = 0
    last_correct: Optional[bool] = None  # None: the player did not answer the last closed question
    rank: int = 0
    seq: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.player_id, "name": self.name, "score": self.score, "rank": self.rank,
                "correct": self.correct, "streak": self.streak, "last_points": self.last_points,
                "last_correct": self.last_correct}


@dataclass
class Game:
    game_id: str
    lobby_id: str
    run: int
    phase: str = ""  # none until its first phase record
    item_id: str = ""  # the item the phase is about (the lobby, a question, the podium)
    players: dict[str, Player] = field(default_factory=dict)
    answers: dict[tuple[str, str], Answer] = field(default_factory=dict)
    runs: dict[str, QuestionRun] = field(default_factory=dict)

    # ------------------------------------------------------------ records

    def apply(self, rec: dict[str, Any]) -> None:
        """Apply one record — live and on replay alike. Unknown or stale records are ignored."""
        op, at = rec.get("op"), int(rec.get("at") or 0)
        if op == "join":
            if rec["player_id"] not in self.players:
                self.players[rec["player_id"]] = Player(rec["player_id"], rec["name"], rec["secret"],
                                                        seq=len(self.players), key=rec.get("key"),
                                                        source=rec.get("source") or PHONE)
        elif op == "kick":
            if rec["player_id"] in self.players:
                self.players[rec["player_id"]].kicked = True
        elif op == "answer":
            key = (rec["player_id"], rec["item_id"])
            if key not in self.answers:  # first one wins, on replay too
                self.answers[key] = Answer(rec["player_id"], rec["item_id"], int(rec["choice"]),
                                           int(rec["elapsed_ms"]), at)
        elif op == "lock":
            run = self.runs.get(rec["item_id"])
            if run is not None and run.open:
                run.closed_at = at
        elif op == "phase" and rec.get("phase") in PHASES:
            phase, item_id = rec["phase"], rec["item_id"]
            run = self.runs.get(item_id)
            if phase == "question" and run is None:
                run = self.runs[item_id] = QuestionRun(item_id, at, int(rec["deadline_ms"]))
            if run is not None and phase in ("question", "reveal", "leaderboard"):
                if phase != "question" and run.open:
                    run.closed_at = at
                run.shown = phase
            self.phase, self.item_id = phase, item_id
        # "game" and "resume" change nothing in the game itself.

    # ----------------------------------------------------------- decisions

    def active(self) -> list[Player]:
        return [p for p in self.players.values() if not p.kicked]

    def unique_name(self, nickname: Any) -> str:
        """``nickname`` cleaned, with `` (2)``, `` (3)``… when an active player already has it."""
        base = clean_name(nickname)
        taken = {p.name.casefold() for p in self.active()}
        name, n = base, 2
        while name.casefold() in taken:
            name, n = f"{base} ({n})", n + 1
        return name

    def by_key(self, key: Optional[str]) -> Optional[Player]:
        if not key:
            return None
        return next((p for p in self.players.values() if p.key == key and not p.kicked), None)

    def check_player(self, player_id: str, secret: Optional[str], *, trusted: bool = False) -> Optional[str]:
        """``None`` when the player may act, else ``UNKNOWN_PLAYER`` or ``KICKED``."""
        p = self.players.get(player_id)
        if p is None or (not trusted and secret != p.secret):
            return UNKNOWN_PLAYER
        return KICKED if p.kicked else None

    def decide_answer(self, player_id: str, secret: Optional[str], item_id: str, choice: Any,
                      question: QuizQuestion, now: int, client_ms: Optional[int], *,
                      trusted: bool = False) -> tuple[AnswerResult, Optional[dict[str, Any]]]:
        """The answer's result, and the record to apply and log when it is accepted (``None`` otherwise).

        ``client_ms`` is how long the phone showed the buttons before the tap; it is clamped to
        ``[0, min(time limit, time since the question opened)]`` — a phone cannot have waited
        longer than the server has been asking. ``None`` (the chat fallback) scores the server's
        own elapsed time.
        """
        refused = self.check_player(player_id, secret, trusted=trusted)
        if refused:
            return AnswerResult(refused, item_id), None
        prior = self.answers.get((player_id, item_id))
        if prior is not None:  # checked before the clock: a retried ack after 00:00 is still an ack
            state = ACCEPTED if _choice(choice) == prior.choice else DUPLICATE
            return AnswerResult(state, item_id, prior.choice, prior.elapsed_ms), None
        pick = _choice(choice)
        if pick is None or not question.answers[pick - 1]:
            raise QuizError(422, "bad_choice", f"Answer {choice!r} is not one of this question's answers")
        run = self.runs.get(item_id)
        if run is None:
            return AnswerResult(NOT_OPEN, item_id), None
        if not run.open or now > run.deadline_ms + GRACE_MS:
            return AnswerResult(TOO_LATE, item_id), None
        window = min(question.time_limit * 1000, max(0, now - run.opened_at))
        elapsed = window if client_ms is None else max(0, min(int(client_ms), window))
        rec = {"op": "answer", "player_id": player_id, "item_id": item_id, "choice": pick,
               "elapsed_ms": elapsed, "client_ms": client_ms}
        return AnswerResult(ACCEPTED, item_id, pick, elapsed), rec

    # ------------------------------------------------------------- derived

    def closed_runs(self, questions: dict[str, QuizQuestion]) -> list[QuestionRun]:
        """The closed questions still in the plan, in the order they were asked."""
        return sorted((r for r in self.runs.values() if not r.open and r.item_id in questions),
                      key=lambda r: r.opened_at)

    def standings(self, questions: dict[str, QuizQuestion]) -> list[Standing]:
        """Every active player's score, streak and rank, best first (see the module docstring)."""
        runs = self.closed_runs(questions)
        out = []
        for p in self.active():
            s = Standing(p.id, p.name, seq=p.seq)
            for run in runs:
                q = questions[run.item_id]
                ans = self.answers.get((p.id, run.item_id))
                right = ans is not None and ans.choice in q.correct
                s.last_points = points(q, right, ans.elapsed_ms) if ans is not None else 0
                s.last_correct = right if ans is not None else None
                s.score += s.last_points
                if right:
                    s.correct += 1
                    s.streak += 1
                    s.correct_ms += ans.elapsed_ms
                else:
                    s.streak = 0
            out.append(s)
        out.sort(key=lambda s: (-s.score, s.correct_ms, s.seq))
        for n, s in enumerate(out, start=1):
            s.rank = n
        return out

    def distribution(self, item_id: str) -> list[int]:
        """How many active players picked each answer (``ANSWERS`` slots)."""
        active = {p.id for p in self.active()}
        counts = [0] * ANSWERS
        for (pid, iid), ans in self.answers.items():
            if iid == item_id and pid in active:
                counts[ans.choice - 1] += 1
        return counts

    def answered(self, item_id: str) -> int:
        active = {p.id for p in self.active()}
        return sum(1 for (pid, iid) in self.answers if iid == item_id and pid in active)

    def snapshot(self, order: list[str], questions: dict[str, QuizQuestion]) -> dict[str, Any]:
        """The ``quiz`` field of the live snapshot. The correct answer, the distribution and
        the scores of a question appear only once it is closed (reveal and after)."""
        players = self.active()
        out: dict[str, Any] = {
            "game_id": self.game_id, "lobby_id": self.lobby_id, "phase": self.phase, "item_id": self.item_id,
            "question_index": order.index(self.item_id) if self.item_id in order else None,
            "question_count": len(order), "deadline_ms": None, "answered_count": 0,
            "player_count": len(players), "players": [{"id": p.id, "name": p.name, "source": p.source} for p in players],
        }
        run, q = self.runs.get(self.item_id), questions.get(self.item_id)
        if run is not None and q is not None:
            out.update(deadline_ms=run.deadline_ms, opened_at_ms=run.opened_at, time_limit=q.time_limit,
                       points=q.points, answered_count=self.answered(self.item_id))
            if not run.open:
                out.update(distribution=self.distribution(self.item_id), correct=list(q.correct))
        if self.phase in ("leaderboard", "podium"):
            out["leaderboard"] = [s.as_dict() for s in self.standings(questions)]
        return out

    def player_view(self, player_id: str, order: list[str], questions: dict[str, QuizQuestion]) -> dict[str, Any]:
        """What one phone shows: the phase, which tiles to draw, its own answer — and only after
        the question closes, whether it was right, its points and its rank."""
        p = self.players[player_id]
        out: dict[str, Any] = {
            "game_id": self.game_id, "player_id": p.id, "name": p.name, "kicked": p.kicked,
            "phase": self.phase, "item_id": self.item_id,
            "question_index": order.index(self.item_id) if self.item_id in order else None,
            "question_count": len(order),
        }
        run, q = self.runs.get(self.item_id), questions.get(self.item_id)
        if run is not None and q is not None:
            ans = self.answers.get((p.id, self.item_id))
            out.update(deadline_ms=run.deadline_ms, open=run.open,
                       tiles=[n for n in range(1, ANSWERS + 1) if q.answers[n - 1]],
                       answer={"choice": ans.choice, "elapsed_ms": ans.elapsed_ms} if ans else None)
            if not run.open and ans is not None:
                out["answer"]["correct"] = ans.choice in q.correct
        if self.phase != "question":
            mine = next((s for s in self.standings(questions) if s.player_id == p.id), None)
            if mine is not None:
                out.update(score=mine.score, rank=mine.rank, streak=mine.streak, last_points=mine.last_points)
        return out


def _choice(choice: Any) -> Optional[int]:
    """``2``, ``"2"`` → 2; anything outside 1–``ANSWERS`` → ``None``."""
    try:
        n = int(str(choice).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= ANSWERS else None
