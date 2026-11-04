"""The rolling summary and the call caps: what keeps a long session's hints informed and cheap.

The hint prompt carries only the last minutes of the transcript verbatim. Every
``summary_interval_s`` the copilot folds the lines heard since the last refresh into a summary of
the whole conversation so far (at most ``SUMMARY_WORDS`` words), kept in
``live/copilot/summary.json`` and added to the hint prompt's stable prefix. The model reads the
previous summary plus only the new lines, so a refresh costs the same at minute 10 as at minute 90.

``RateLimit`` caps the model calls of each kind per sliding hour, so a chatty call cannot run them
up: past the cap nothing is asked until the oldest call in the window is an hour old.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence

from src.copilot.hints import (
    LANGUAGE_NAMES,
    HintFormatError,
    Line,
    interviewee_clause,
    speaker_labels,
    transcript_text,
)

SUMMARY_FILE = "summary.json"
SUMMARY_WORDS = 300
# 300 words is about 450 tokens in English and more in Spanish; the margin keeps a summary from being cut mid-sentence.
SUMMARY_MAX_TOKENS = 900
HOUR_S = 3600.0


def summary_messages(language: str, previous: str, lines: Sequence[Line], now: float,
                     mode: str = "workshop", interviewee: str = "") -> list[dict[str, str]]:
    """The chat messages that fold ``lines`` into ``previous``."""
    lang = LANGUAGE_NAMES.get(language, "English")
    labels = speaker_labels(mode, interviewee)
    if mode == "interview":
        system = (
            "You keep the running summary of a remote one-to-one interview for the interviewer. In the transcript, "
            f"'me' is the interviewer and {interviewee_clause(interviewee)}. Rewrite the summary so far to "
            "include the new transcript: the topics covered, what the interviewee said about each, the questions "
            "they have not answered yet and points they came back to. Drop small talk. "
            f"Answer with the summary only, plain text, at most {SUMMARY_WORDS} words, in {lang}."
        )
    else:
        system = (
            "You keep the running summary of a live session (a workshop, an interview or a meeting) for its "
            "facilitator. In the transcript, 'me' is the facilitator and 'them' is everyone else on the call. "
            "Rewrite the summary so far to include the new transcript: the topics covered, what was decided, "
            "open questions and points people raised more than once. Drop small talk. "
            f"Answer with the summary only, plain text, at most {SUMMARY_WORDS} words, in {lang}."
        )
    user = (f"Summary so far:\n{previous or '(none yet: this is the start of the session)'}\n\n"
            f"New transcript since then, oldest first (time before now):\n{transcript_text(lines, now, labels)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_summary(text: str) -> str:
    """The summary in a model's answer, cut to ``SUMMARY_WORDS`` words; ``HintFormatError`` if empty."""
    words = text.split()
    if not words:
        raise HintFormatError("the summary is empty")
    out = " ".join(words[:SUMMARY_WORDS])
    return out.rstrip(".,;:!?…") + "…" if len(words) > SUMMARY_WORDS else out


class RateLimit:
    """At most ``per_hour`` calls in any sliding hour (0 = no cap). Used on the server's loop only."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.calls: deque[float] = deque()

    def _prune(self) -> None:
        cutoff = self.clock() - HOUR_S
        while self.calls and self.calls[0] <= cutoff:
            self.calls.popleft()

    def full(self, per_hour: int) -> bool:
        self._prune()
        return per_hour > 0 and len(self.calls) >= per_hour

    def take(self, per_hour: int) -> bool:
        """Count one call now; False (and nothing counted) when the cap is reached."""
        if self.full(per_hour):
            return False
        self.calls.append(self.clock())
        return True

    def refund(self) -> None:
        """The last call counted never reached the hub (a newer request superseded it)."""
        if self.calls:
            self.calls.pop()

    def retry_in(self) -> float:
        """Seconds until the oldest call in the window leaves it."""
        self._prune()
        return max(0.0, self.calls[0] + HOUR_S - self.clock()) if self.calls else 0.0
