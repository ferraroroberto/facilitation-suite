"""The quiz's Zoom-chat fallback (#54): ``A``–``D`` typed in the chat answer the open question.

If the public player page fails mid-session, the facilitator says "type your
answer in the chat" and the game goes on, scored by the same engine. The
Zoom chat reader (or the simulator) already delivers every message with its
sender; ``ChatAnswers`` subscribes to ``ChatHub.listeners`` like
``CaptureService`` does.

A message is an answer when all of these hold:

- the lobby's "answers typed in the chat count too" switch (``accept_chat``,
  default on) is on for this game;
- a question is on stage and still open (its ``question`` phase — never on
  the lobby, a reveal, a leaderboard or the podium);
- its trimmed text is exactly one of ``A B C D a b c d 1 2 3 4``, optionally
  followed by punctuation (``B``, ``b.``, ``3!``) — ``parse_choice``;
- the letter is one of this question's answers (``D`` on a three-answer
  question is ignored);
- it is not the facilitator's own message ("You") — unless the presenter's
  rehearsal switch (``count_own``) is on, so a rehearsal alone works.

The sender plays as a chat player: joined on their first answer with
``source="chat"``, their Zoom name as the nickname (``(2)`` added when a
phone player already has it, so both stay distinct on the leaderboard) and
the join key ``chat:<sender>``, so every later message of that sender is the
same player. The response time is the chat's ``received_at`` minus the
question's opening, clamped to the question's window by the engine. The
first answer wins, as on a phone: a second message changes nothing. A chat
player the host kicked stays out.

Known trade-off (README): chat answers are visible to everyone in the chat.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Optional

from src.chat.hub import ChatHub
from src.quiz.engine import ACCEPTED, CHAT, Game
from src.quiz.model import QuizError, QuizQuestion
from src.quiz.service import QuizService

logger = logging.getLogger(__name__)

CHAT_KEY = "chat:"  # join key prefix: chat:<Zoom name>
_ANSWER = re.compile(r"([A-Da-d1-4])[.!?,;:)…]*")
_LETTERS = "abcd"


def parse_choice(text: Any) -> Optional[int]:
    """``"B"``, ``" b. "``, ``"2!"`` → 2; anything else (``"B is right"``, ``"E"``, ``"5"``) → ``None``."""
    m = _ANSWER.fullmatch(str(text or "").strip())
    if m is None:
        return None
    ch = m.group(1).lower()
    return int(ch) if ch.isdigit() else _LETTERS.index(ch) + 1


class ChatAnswers:
    """Scores chat messages as quiz answers (see the module docstring)."""

    def __init__(self, quiz: QuizService, chat: ChatHub, count_own: Callable[[], bool] = lambda: False) -> None:
        self.quiz, self.count_own = quiz, count_own
        chat.listeners.append(self.on_messages)

    def on_messages(self, records: list[dict[str, Any]]) -> None:
        """A batch of new chat records (on the hub's loop, from ``ChatHub.ingest``)."""
        opened = self.quiz.open_question()
        if opened is None:
            return
        game, scope, question = opened
        if not self.quiz.accepts_chat(scope):
            return
        for rec in records:
            try:
                self._one(game, question, rec)
            except QuizError as exc:  # a bad nickname: skip this message, never the batch
                logger.info("ℹ️ quiz chat: message %s skipped (%s)", rec.get("id"), exc)

    def _one(self, game: Game, question: QuizQuestion, rec: dict[str, Any]) -> None:
        if rec.get("own") and not self.count_own():
            return
        choice = parse_choice(rec.get("text"))
        if choice is None or not question.answers[choice - 1]:
            return
        item_id = game.item_id
        run = game.runs[item_id]
        received = int(rec.get("received_at") or self.quiz.clock())
        if received < run.opened_at:
            return  # typed before the question opened
        player_id = self._player(game, str(rec.get("sender") or ""))
        if player_id is None:
            return
        res = self.quiz.answer_trusted(player_id, item_id, choice, elapsed_ms=received - run.opened_at)
        if res.state == ACCEPTED:
            logger.info("ℹ️ quiz %s: chat answer from %s on %s (%d ms)", game.game_id, player_id, item_id,
                        res.elapsed_ms or 0)

    def _player(self, game: Game, sender: str) -> Optional[str]:
        """The sender's chat player in this game — joined now on a first answer; ``None`` if kicked."""
        key = CHAT_KEY + sender
        known = next((p for p in game.players.values() if p.key == key), None)
        if known is not None:
            return None if known.kicked else known.id
        joined = self.quiz.join(sender, game.game_id, key, source=CHAT)
        return joined.player_id
