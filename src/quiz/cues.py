"""The quiz's sound cues (#53): a phase change on stage plays its cue through ``MusicService``.

Played on this PC (the server-side music, #65), never in the stage browser.
A cue is a file in the session folder's ``audio/`` — any of ``.mp3``,
``.ogg``, ``.wav``, ``.flac``; none there means silence, never an error:

- ``quiz-lobby.*`` — the lobby; loops for as long as the lobby is on stage;
- ``quiz-countdown.*`` — a question opens (plays once);
- ``quiz-reveal.*`` — the answers are revealed (plays once);
- ``quiz-podium.*`` — the podium (plays once).

Every other change (the leaderboard, leaving the quiz, a cue-less phase)
fades out a cue that is still sounding, so the lobby loop never runs into a
question. Precedence is ``MusicService.cue``'s: an item's own music or the
presenter's ad-hoc music always wins — the cue is skipped and that music
plays on untouched. Joins and answers change nothing: only a new
``(session, game, phase, item)`` does.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.live.hub import LiveHub
from src.music.service import MusicService
from src.quiz.service import QuizService

logger = logging.getLogger(__name__)

# phase → (cue file name in audio/, loop)
CUES: dict[str, tuple[str, bool]] = {
    "lobby": ("quiz-lobby", True),
    "question": ("quiz-countdown", False),
    "reveal": ("quiz-reveal", False),
    "podium": ("quiz-podium", False),
}


class QuizCues:
    def __init__(self, live: LiveHub, quiz: QuizService, music: MusicService) -> None:
        self.live, self.quiz, self.music = live, quiz, music
        self.key: Optional[tuple[Any, ...]] = None  # the (session, game, phase, item) last heard
        quiz.change_listeners.append(self.check)  # every record: phases, and a session going live or reset
        live.item_listeners.append(lambda prev, cur: self.check())  # moving off the quiz changes no game

    def check(self) -> None:
        """Play the cue of a phase that just changed (or fade out a cue the new phase has none for)."""
        try:
            game = self.quiz.game_on_stage()
            key = (self.live.session_id, game.game_id, game.phase, game.item_id) if game and game.phase else None
            if key == self.key:
                return
            self.key = key
            name, loop = CUES.get(key[2], (None, False)) if key else (None, False)
            if not (name and self.music.cue(name, loop=loop)):
                self.music.end_cue()
        except Exception:  # noqa: BLE001 — a sound problem must never stop the game or the deck
            logger.exception("❌ quiz: the sound cue failed")
