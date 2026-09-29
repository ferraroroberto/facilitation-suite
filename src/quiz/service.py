"""The live quiz (#51): the game engine wired to the live hub, persisted to ``live/quiz.jsonl``.

Wired like ``CaptureService``: it hears items change, sessions go live and
resets, adds ``quiz`` to every ``/ws`` snapshot and registers its actions. It
also takes the hub's ``next`` while a question has phases left, and the
hub's ``next`` and ``prev`` while a podium has places to reveal or hide.

**Game scope.** A ``quiz_lobby`` item and the ``quiz`` items after it, in run
order, up to the next ``quiz_podium`` (or the next lobby), are one game. The
first time the stage reaches any item of that scope a game opens:
``<lobby id>-1``. Coming back to the lobby later resumes that same game —
nothing is ever discarded by moving around; ``new_game()`` (``quiz_new_game``
on the lobby) starts ``<lobby id>-2`` for a replay. A ``quiz`` item outside
any scope is shown as a plain item.

**Phases and ``next``.** Entering a question for the first time opens it
(``question``, deadline = now + its time limit). Then ``next`` (every control
surface's "next") steps ``question → reveal → leaderboard`` and only from
``leaderboard`` moves on to the next plan item; time up (plus
``engine.GRACE_MS``) or ``quiz_lock`` reveals by themselves. On the
**podium** (#89) ``next`` reveals one place at a time — 3rd, 2nd, then 1st
(only the places that exist with fewer than three players) — and only once
1st shows moves on; ``prev`` hides the last place shown, and with none shown
goes to the previous item. The step (``podium_step`` in ``state.quiz``, with
``podium_places``) is a ``podium`` record, so a restart keeps it; entering
the podium from another phase of the game starts it at 0. On a lobby and
every other item ``next`` and ``prev`` behave exactly as before.
**Space** (the ``space`` action) on a question that is still open locks it
(the same as ``quiz_lock``); anywhere else Space is the capture or the timer
as before. Off the podium ``prev`` is never taken: it goes to the previous
item. Leaving a question while it is still open locks it (answers only count while the stage
shows the question); coming back shows its reveal or leaderboard again, never
a second chance to answer.

**Durability.** Every join, resume, kick, answer and phase change is one
record in ``live/quiz.jsonl`` (see ``engine.py``); phase changes also go to
``live/events.jsonl`` (``quiz_phase``) for the session timeline. When a
session goes live, the games are rebuilt by replaying ``quiz.jsonl`` — scores
are derived, never stored — and a question whose time ran out while the
server was down is revealed at once. A failed write keeps the record in
memory, shows ``write_error`` and is written with the next record.

**In-process API** for the player page (Step 4) and the chat fallback
(Step 6). Every call must run on the live hub's event loop (an ``async``
route, or ``loop.call_soon_threadsafe``) — the engine takes no lock:

- ``join(nickname, game_id=None, key=None, *, source="phone") -> JoinResult``
  — ``joined`` with ``player_id`` + ``secret`` (keep both on the phone), or
  ``no_game`` / ``closed``. A retry with the same ``key`` returns the same
  player. The chat fallback joins its players with ``source="chat"``.
  Raises ``QuizError`` 422 ``bad_nickname`` for an empty nickname.
- ``resume(player_id, secret) -> JoinResult`` — ``resumed`` (same player,
  same score), ``unknown_player`` (also for a wrong secret) or ``kicked``.
- ``answer(player_id, secret, item_id, choice, elapsed_ms=None) ->
  AnswerResult`` — ``accepted`` (a retry of the same answer gets the same
  ack), ``duplicate``, ``too_late``, ``not_open``, ``unknown_player`` or
  ``kicked``. ``choice`` is 1–4; ``elapsed_ms`` is the time since the buttons
  rendered on that phone. Raises ``QuizError`` 422 ``bad_choice`` for a
  choice that is not one of the question's answers.
- ``answer_trusted(player_id, item_id, choice, elapsed_ms=None)`` — the
  same without the secret, for in-process callers that own the identity
  (the chat fallback). Never reachable from a public route.
- ``kick(player_id)``, ``lock()``, ``new_game()`` — the host's controls
  (also the actions ``quiz_kick/<id>``, ``quiz_lock``, ``quiz_new_game``).
- ``player_view(player_id, secret) -> Optional[dict]`` — what that phone
  shows; ``None`` for an unknown player or a wrong secret. On a question it
  carries ``question``, ``tiles`` (the answer numbers) and ``answers`` (their
  texts, in tile order) — all shown on the stage anyway; ``correct`` (the
  answer numbers) and ``answer.correct`` only once the question has closed.
- ``join_pin(pin, nickname, key=None) -> JoinResult`` — ``join`` into the
  game with that PIN: ``wrong_pin`` when no game of the live session has it,
  else ``join``'s own states (``no_game`` when that game is not on stage).
- ``change_listeners`` — callables run after every change to a game (every
  record: a join, an answer, a phase…) and when the live session changes, so
  a player socket can push.
- ``open_question()`` — the game, scope and question taking answers on
  stage now; the chat fallback (``chat.py``) reads it.

**Join PIN.** Every game gets a 6-digit PIN (a ``pin`` record right after
its ``game`` record, so it survives a restart); ``state.quiz`` carries it
with ``join_url`` (``quiz.public_url`` + ``/play?pin=…``, ``None`` while the
public URL is not configured), ``listener`` (the player listener is up) and
``reach`` (the last public-link check, ``reach.py``: ``state`` ok |
listener_down | tunnel_down | public_unreachable | not_configured | unknown, ``label``,
``detail``, ``checked_at``). The server sets ``public_url``, ``listener_up``
and ``reach``.

``state.quiz`` also carries ``accept_chat``: the lobby's "answers typed in
the chat count too" switch for this game (default on).
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.activities.registry import options_with_defaults
from src.jsonl import read_jsonl
from src.live.actions import Action, register
from src.live.hub import LiveError, LiveHub, now_ms
from src.quiz.engine import (
    CLOSED,
    GRACE_MS,
    JOINED,
    NO_GAME,
    NOT_OPEN,
    PHONE,
    RESUMED,
    UNKNOWN_PLAYER,
    AnswerResult,
    Game,
)
from src.quiz.model import QuizError, QuizQuestion, question_from_item

logger = logging.getLogger(__name__)

QUIZ_FILE = "quiz.jsonl"
PUSH_EVERY_S = 0.25  # joins and answers in a burst: at most four snapshot pushes a second
LOBBY, QUESTION, PODIUM = "quiz_lobby", "quiz", "quiz_podium"
QUIZ_TYPES = (LOBBY, QUESTION, PODIUM)
WRONG_PIN = "wrong_pin"  # join_pin: no game of the live session has that PIN
PIN_DIGITS = 6


@dataclass
class Scope:
    """One game's items in the current run: its lobby, its questions in order, its podium."""

    lobby_id: str
    order: list[str] = field(default_factory=list)  # the valid questions' item ids
    questions: dict[str, QuizQuestion] = field(default_factory=dict)
    invalid: dict[str, str] = field(default_factory=dict)  # item id → why it cannot be played
    podium_id: Optional[str] = None


@dataclass
class JoinResult:
    state: str
    game_id: Optional[str] = None
    player_id: Optional[str] = None
    secret: Optional[str] = None
    name: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "game_id": self.game_id, "player_id": self.player_id,
                "secret": self.secret, "name": self.name}


def scopes_of(items: list[dict[str, Any]]) -> dict[str, Scope]:
    """Item id → its game's scope, for every lobby, question and podium of the run ``items``.

    The one reading of the plan's game scopes: the live service and the
    session results (``results.py``) both call it.
    """
    out: dict[str, Scope] = {}
    scope: Optional[Scope] = None
    for it in items:
        kind = it.get("type") if it.get("kind") == "activity" else None
        if kind == LOBBY:
            scope = Scope(it["id"])
            out[it["id"]] = scope
        elif kind == QUESTION and scope is not None:
            try:
                q = question_from_item({**it, "options": options_with_defaults(QUESTION, it.get("options") or {})})
            except QuizError as exc:
                logger.warning("⚠️ quiz: %s — it is shown but cannot be played", exc)
                scope.invalid[it["id"]] = str(exc)
            else:
                scope.order.append(it["id"])
                scope.questions[it["id"]] = q
            out[it["id"]] = scope
        elif kind == PODIUM and scope is not None:
            scope.podium_id = it["id"]
            out[it["id"]] = scope
            scope = None
    return out


def replay(records: list[dict[str, Any]]) -> dict[str, Game]:
    """Every game of ``quiz.jsonl``'s ``records``, rebuilt through ``Game.apply`` in the order they opened.

    The one replay: the live service after a restart and the session results
    both call it, so neither can score a game differently.
    """
    games: dict[str, Game] = {}
    for rec in records:
        gid = rec.get("game")
        if rec.get("op") == "game" and gid:
            if gid in games:
                continue  # written twice after a failed write: the game is already open
            games[gid] = Game(gid, str(rec.get("lobby_id")), int(rec.get("run") or 1))
        elif gid in games:
            try:
                games[gid].apply(rec)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("⚠️ quiz: a damaged %s record skipped (%s)", QUIZ_FILE, exc)
    return games


class QuizService:
    def __init__(self, live: LiveHub, clock: Callable[[], int] = now_ms) -> None:
        self.live, self.clock = live, clock
        self.games: dict[str, Game] = {}
        self.latest: dict[str, str] = {}  # lobby id → its newest game id
        self.change_listeners: list[Callable[[], None]] = []
        self.public_url: Callable[[], str] = lambda: ""  # quiz.public_url (set by the server)
        self.listener_up: Callable[[], bool] = lambda: False  # the player listener serves (set by the server)
        self.reach: Callable[[], Optional[dict[str, Any]]] = lambda: None  # the public link check (``reach.py``)
        self._session: Optional[str] = None
        self._scopes: tuple[Any, dict[str, Scope]] = (None, {})
        self._pending: list[dict[str, Any]] = []  # records not yet written to quiz.jsonl
        self._reveal: Optional[Any] = None  # the time-up handle of the open question
        self._push_pending = False
        live.extra_state.append(self.state_fields)
        live.item_listeners.append(self._on_item)
        live.session_listeners.append(self._on_session)
        live.reset_listeners.append(self._forget)
        live.next_handlers.append(self._on_next)
        live.prev_handlers.append(self._on_prev)
        live.space_handlers.append(self._on_space)
        register(Action("quiz_lock", "Quiz: lock answers", lambda h, a: self.lock()))
        register(Action("quiz_kick", "Quiz: remove a player", lambda h, a: self.kick(str(a)), arg="id", stream_deck=False))
        register(Action("quiz_new_game", "Quiz: play again (a new game)", lambda h, a: self.new_game(), stream_deck=False))

    # ------------------------------------------------------------- scopes

    def scopes(self) -> dict[str, Scope]:
        """Item id → its game's scope, for every lobby, question and podium in a game (per plan revision)."""
        key = (self.live.session_id, self.live.plan_rev)
        if self._scopes[0] != key:
            self._scopes = (key, scopes_of(self.live.items))
        return self._scopes[1]

    def _current(self) -> tuple[Optional[dict[str, Any]], Optional[Scope], Optional[Game]]:
        """The item on stage, its game's scope and that game (each ``None`` when there is none)."""
        self._load()
        cur = self.live.current()
        scope = self.scopes().get(cur["id"]) if cur else None
        game = self.games.get(self.latest.get(scope.lobby_id, "")) if scope else None
        return cur, scope, game

    # ------------------------------------------------------------ queries

    def state_fields(self) -> dict[str, Any]:
        cur, scope, game = self._current()
        if cur is None or scope is None:
            return {"quiz": None}
        if cur["id"] in scope.invalid:
            return {"quiz": {"game_id": game.game_id if game else None, "phase": None, "item_id": cur["id"],
                             "error": scope.invalid[cur["id"]]}}
        if game is None:
            return {"quiz": None}
        return {"quiz": {**game.snapshot(scope.order, scope.questions), "join_url": self.join_url(game.pin),
                         "listener": self.listener_up(), "accept_chat": self.accepts_chat(scope),
                         "reach": self.reach()}}

    def accepts_chat(self, scope: Scope) -> bool:
        """The lobby's "answers typed in the chat count too" switch (default on)."""
        lobby = self.live.item_by_id(scope.lobby_id) or {}
        return bool(options_with_defaults(LOBBY, lobby.get("options") or {}).get("accept_chat", True))

    def open_question(self) -> Optional[tuple[Game, Scope, QuizQuestion]]:
        """The question on stage while it takes answers (its ``question`` phase, still open), else ``None``."""
        cur, scope, game = self._current()
        if cur is None or scope is None or game is None or cur["id"] not in scope.questions:
            return None
        run = game.runs.get(cur["id"])
        if game.phase != "question" or game.item_id != cur["id"] or run is None or not run.open:
            return None
        return game, scope, scope.questions[cur["id"]]

    def podium_on_stage(self) -> Optional[Game]:
        """The game whose podium is on stage now (``None`` anywhere else)."""
        cur, scope, game = self._current()
        if cur is None or scope is None or game is None or cur["id"] != scope.podium_id:
            return None
        return game if (game.phase, game.item_id) == ("podium", cur["id"]) else None

    def game_on_stage(self) -> Optional[Game]:
        """The game of the quiz item on stage (``None`` off a quiz)."""
        return self._current()[2]

    def join_url(self, pin: str) -> Optional[str]:
        """``quiz.public_url`` + ``/play?pin=…``; ``None`` while the public URL is not configured."""
        base = (self.public_url() or "").strip().rstrip("/")
        return f"{base}/play?pin={pin}" if base and pin else None

    def game_by_pin(self, pin: Any) -> Optional[Game]:
        """The live session's game with that join PIN (any run, on stage or not)."""
        self._load()
        pin = "".join(str(pin or "").split())
        return next((g for g in self.games.values() if g.pin == pin), None) if pin else None

    def player_view(self, player_id: str, secret: str) -> Optional[dict[str, Any]]:
        """What one phone shows now; ``None`` for an unknown player or a wrong secret."""
        game = self._game_of(player_id)
        if game is None or game.check_player(player_id, secret) == UNKNOWN_PLAYER:
            return None
        scope = next((s for s in self.scopes().values() if s.lobby_id == game.lobby_id), None)
        return game.player_view(player_id, scope.order if scope else [], scope.questions if scope else {})

    def _game_of(self, player_id: str) -> Optional[Game]:
        self._load()
        return next((g for g in self.games.values() if player_id in g.players), None)

    # ---------------------------------------------------------- players

    def join(self, nickname: Any, game_id: Optional[str] = None, key: Optional[str] = None, *,
             source: str = PHONE) -> JoinResult:
        """A new player in the game on stage (see the module docstring). ``source`` is ``phone``
        (the player page) or ``chat`` (the Zoom-chat fallback) — the results' leaderboard shows it."""
        _, scope, game = self._current()
        if game is None or scope is None or (game_id and game_id != game.game_id):
            return JoinResult(NO_GAME, game_id)
        known = game.by_key(key)
        if known is not None:
            return JoinResult(JOINED, game.game_id, known.id, known.secret, known.name)
        if game.phase == "podium":
            return JoinResult(CLOSED, game.game_id)
        name = game.unique_name(nickname)
        player_id = self._new_player_id()
        secret = secrets.token_urlsafe(16)
        self._record(game, {"op": "join", "player_id": player_id, "name": name, "secret": secret, "key": key,
                            "source": source})
        logger.info("ℹ️ quiz %s: %s joined (%d players)", game.game_id, player_id, len(game.active()))
        self._changed(soon=True)
        return JoinResult(JOINED, game.game_id, player_id, secret, name)

    def join_pin(self, pin: Any, nickname: Any, key: Optional[str] = None) -> JoinResult:
        """``join`` into the game with that PIN (see the module docstring)."""
        game = self.game_by_pin(pin)
        if game is None:
            return JoinResult(WRONG_PIN)
        return self.join(nickname, game.game_id, key)

    def resume(self, player_id: str, secret: str) -> JoinResult:
        """The same player again (a reopened tab, a locked phone, a network switch)."""
        game = self._game_of(player_id)
        if game is None:
            return JoinResult(UNKNOWN_PLAYER)
        refused = game.check_player(player_id, secret)
        if refused:
            return JoinResult(refused, game.game_id, player_id)
        self._record(game, {"op": "resume", "player_id": player_id})
        p = game.players[player_id]
        return JoinResult(RESUMED, game.game_id, p.id, p.secret, p.name)

    def kick(self, player_id: str) -> None:
        game = self._game_of(player_id)
        if game is None or game.players[player_id].kicked:
            raise QuizError(404, "unknown_player", f"No player {player_id!r} in a quiz")
        self._record(game, {"op": "kick", "player_id": player_id})
        logger.info("ℹ️ quiz %s: %s removed by the host", game.game_id, player_id)
        self._changed()

    def _new_pin(self) -> str:
        """A 6-digit PIN (no leading zero) no other game of the live session has."""
        taken = {g.pin for g in self.games.values()}
        while True:
            pin = str(10 ** (PIN_DIGITS - 1) + secrets.randbelow(9 * 10 ** (PIN_DIGITS - 1)))
            if pin not in taken:
                return pin

    def _new_player_id(self) -> str:
        while True:
            pid = "p" + secrets.token_hex(4)
            if self._game_of(pid) is None:
                return pid

    # ---------------------------------------------------------- answers

    def answer(self, player_id: str, secret: str, item_id: str, choice: Any,
               elapsed_ms: Optional[int] = None) -> AnswerResult:
        """A phone's answer (see the module docstring for the result states)."""
        return self._answer(player_id, secret, item_id, choice, elapsed_ms, trusted=False)

    def answer_trusted(self, player_id: str, item_id: str, choice: Any,
                       elapsed_ms: Optional[int] = None) -> AnswerResult:
        """An answer from an in-process caller that owns the identity (the chat fallback): no secret."""
        return self._answer(player_id, None, item_id, choice, elapsed_ms, trusted=True)

    def _answer(self, player_id: str, secret: Optional[str], item_id: str, choice: Any,
                elapsed_ms: Optional[int], *, trusted: bool) -> AnswerResult:
        game = self._game_of(player_id)
        if game is None:
            return AnswerResult(UNKNOWN_PLAYER, item_id)
        scope = self.scopes().get(item_id)
        question = scope.questions.get(item_id) if scope is not None and scope.lobby_id == game.lobby_id else None
        if question is None:
            refused = game.check_player(player_id, secret, trusted=trusted)
            return AnswerResult(refused or NOT_OPEN, item_id)
        result, rec = game.decide_answer(player_id, secret, item_id, choice, question, self.clock(), elapsed_ms,
                                         trusted=trusted)
        if rec is not None:
            self._record(game, rec)
            self._changed(soon=True)
        return result

    # ------------------------------------------------------------ phases

    def lock(self) -> None:
        """Lock the answers of the question on stage: its reveal."""
        cur, scope, game = self._current()
        run = game.runs.get(cur["id"]) if game and cur else None
        if run is None or not run.open or game.item_id != cur["id"]:
            raise LiveError(409, "no_open_question", "There is no open quiz question on stage")
        self._phase(game, "reveal", cur["id"])
        self._changed()

    def new_game(self) -> None:
        """Play the quiz on stage again: a new game (``<lobby>-<run + 1>``) with no players yet."""
        cur, scope, game = self._current()
        if cur is None or scope is None or cur["id"] != scope.lobby_id:
            raise LiveError(409, "not_a_lobby", "A new game starts from the quiz lobby")
        self._phase(self._open_game(scope.lobby_id, (game.run if game else 0) + 1), "lobby", scope.lobby_id)
        self._changed()

    def _on_next(self) -> bool:
        """The hub's ``next``: on a question with phases left, or a podium with places to reveal,
        step it and take the ``next``."""
        podium = self.podium_on_stage()
        if podium is not None:
            return self._podium_step(podium, +1)
        cur, scope, game = self._current()
        if cur is None or scope is None or game is None or cur["id"] not in scope.questions or game.item_id != cur["id"]:
            return False
        if game.phase == "question":
            self._phase(game, "reveal", cur["id"])
        elif game.phase == "reveal":
            self._phase(game, "leaderboard", cur["id"])
        else:
            return False  # leaderboard: on to the next item
        self._changed()
        return True

    def _on_prev(self) -> bool:
        """The hub's ``prev``: on a podium with a place shown, hide it and take the ``prev``."""
        podium = self.podium_on_stage()
        return podium is not None and self._podium_step(podium, -1)

    def _podium_step(self, game: Game, delta: int) -> bool:
        """Show one podium place more (``+1``) or fewer (``-1``); ``False`` when there is none to step."""
        step = game.podium_shown() + delta
        if not 0 <= step <= game.podium_places():
            return False
        self._record(game, {"op": "podium", "item_id": game.item_id, "step": step})
        logger.info("ℹ️ quiz %s: podium shows %d of %d places", game.game_id, step, game.podium_places())
        self._changed()
        return True

    def _on_space(self) -> bool:
        """The hub's Space: on a question that is still open, lock it and take the key."""
        if self.open_question() is None:
            return False
        self.lock()
        return True

    def _on_item(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        self._load()
        if prev is not None and prev["id"] != cur["id"]:
            scope = self.scopes().get(prev["id"])
            game = self.games.get(self.latest.get(scope.lobby_id, "")) if scope else None
            run = game.runs.get(prev["id"]) if game else None
            if game is not None and run is not None and run.open:
                logger.info("ℹ️ quiz %s: %s left while open — its answers are locked", game.game_id, prev["id"])
                self._record(game, {"op": "lock", "item_id": prev["id"]})
        self._enter(cur)

    def _enter(self, cur: Optional[dict[str, Any]]) -> None:
        """Bring the game in line with the item on stage (a move, or a session going live)."""
        self._cancel_reveal()
        scope = self.scopes().get(cur["id"]) if cur else None
        if cur is None or scope is None or cur["id"] in scope.invalid:
            return
        game = self.games.get(self.latest.get(scope.lobby_id, "")) or self._open_game(scope.lobby_id, 1)
        if cur["id"] == scope.lobby_id:
            self._phase(game, "lobby", cur["id"])
        elif cur["id"] == scope.podium_id:
            self._phase(game, "podium", cur["id"])
        else:
            run = game.runs.get(cur["id"])
            if run is None:
                deadline = self.clock() + scope.questions[cur["id"]].time_limit * 1000
                self._phase(game, "question", cur["id"], deadline_ms=deadline)
            elif run.open and self.clock() > run.deadline_ms + GRACE_MS:
                self._phase(game, "reveal", cur["id"])  # time ran out while the server was down
            else:
                self._phase(game, "question" if run.open else ("reveal" if run.shown == "question" else run.shown),
                            cur["id"])
        run = game.runs.get(cur["id"])
        if run is not None and run.open and game.item_id == cur["id"]:
            self._schedule_reveal(game, cur["id"], run.deadline_ms)

    def _phase(self, game: Game, phase: str, item_id: str, **extra: Any) -> None:
        if not game.pin:  # a new game, or one from a file written before PINs
            self._record(game, {"op": "pin", "pin": self._new_pin()})
        if (game.phase, game.item_id) == (phase, item_id):
            return  # already there (a session going live again)
        self._record(game, {"op": "phase", "phase": phase, "item_id": item_id, **extra})
        self.live.event("quiz_phase", game=game.game_id, phase=phase, item_id=item_id)
        logger.info("ℹ️ quiz %s: %s on %s", game.game_id, phase, item_id)
        if phase != "question":
            self._cancel_reveal()

    def _open_game(self, lobby_id: str, run: int) -> Game:
        game_id = f"{lobby_id}-{run}"
        game = Game(game_id, lobby_id, run)
        self.games[game_id] = game
        self.latest[lobby_id] = game_id
        self._record(game, {"op": "game", "lobby_id": lobby_id, "run": run})
        self.live.event("quiz_game", game=game_id, lobby_id=lobby_id)
        logger.info("✅ quiz: game %s open", game_id)
        return game

    # ------------------------------------------------------------- time up

    def _schedule_reveal(self, game: Game, item_id: str, deadline_ms: int) -> None:
        self._cancel_reveal()
        if self.live.loop is None:
            return
        delay = max(0.0, (deadline_ms + GRACE_MS - self.clock()) / 1000)
        self._reveal = self.live.loop.call_later(delay, self.time_up, game.game_id, item_id)

    def _cancel_reveal(self) -> None:
        if self._reveal is not None:
            self._reveal.cancel()
            self._reveal = None

    def time_up(self, game_id: str, item_id: str) -> None:
        """The question's time (plus the grace) ran out: reveal it if it is still open on stage."""
        self._reveal = None
        game = self.games.get(game_id)
        run = game.runs.get(item_id) if game else None
        if game is None or run is None or not run.open or game.item_id != item_id:
            return
        logger.info("ℹ️ quiz %s: time up on %s", game_id, item_id)
        self._phase(game, "reveal", item_id)
        self._changed()

    # ------------------------------------------------------------- hooks

    def _on_session(self, sid: Optional[str]) -> None:
        self._load()
        if sid is not None:
            self._enter(self.live.current())
        else:
            self._cancel_reveal()
        self._notify()

    def _forget(self) -> None:
        """After a reset: no games (the old run, ``quiz.jsonl`` included, is set aside)."""
        self._cancel_reveal()
        self._session = None
        self._load()
        self._notify()

    def _notify(self) -> None:
        """Run the change listeners (the player sockets); one failing never stops the game."""
        for fn in self.change_listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001 — a listener's bug must not break a join or an answer
                logger.exception("❌ quiz: a change listener failed")

    def _changed(self, *, soon: bool = False) -> None:
        """Tell the views: at once (a phase), or coalesced within ``PUSH_EVERY_S`` (a burst of joins or answers)."""
        if not soon or self.live.loop is None:
            self.live.push_state()
            return
        if self._push_pending:
            return
        self._push_pending = True

        def push() -> None:
            self._push_pending = False
            self.live.push_state()

        self.live.loop.call_later(PUSH_EVERY_S, push)

    # -------------------------------------------------------- durability

    def _path(self) -> Optional[Path]:
        return self.live.folder / "live" / QUIZ_FILE if self.live.folder is not None else None

    def _load(self) -> None:
        """Rebuild every game of the live session by replaying ``quiz.jsonl`` (once per session)."""
        if self.live.session_id == self._session:
            return
        self._session = self.live.session_id
        self.games, self.latest, self._pending = {}, {}, []
        path = self._path()
        if path is None:
            return
        records = read_jsonl(path)
        self.games = replay(records)
        self.latest = {g.lobby_id: gid for gid, g in self.games.items()}
        if records:
            logger.info("✅ quiz: %d records replayed — %d game(s) rebuilt", len(records), len(self.games))

    def _record(self, game: Game, rec: dict[str, Any]) -> None:
        """Apply one record to its game and append it to ``quiz.jsonl`` (kept in memory if the write fails)."""
        rec = {"game": game.game_id, "at": self.clock(), **rec}
        game.apply(rec)
        self._notify()
        path = self._path()
        if path is None:
            return
        self._pending.append(rec)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self._pending))
        except OSError as exc:
            self.live.write_failed(QUIZ_FILE, exc)
            return
        self._pending = []
        if self.live.write_error and QUIZ_FILE in self.live.write_error:
            logger.info("✅ quiz: %s written again — the session folder is writable", QUIZ_FILE)
            self.live.write_error = None
