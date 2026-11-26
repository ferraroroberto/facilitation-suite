"""What the copilot's pipelines share with the service that composes them.

``Run`` is one stretch of Copilot on, ``Status`` the problems and counts the presenter's card shows, and
``CopilotContext`` the way back to the live session. Workers run on their own threads; every change to
this state is made on the server's loop (``CopilotContext.on_loop``).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.config import CopilotConfig
from src.copilot.audio import WavRecorder
from src.copilot.hints import model_chain
from src.copilot.summary import RateLimit
from src.copilot.vad import Chunker
from src.jsonl import JsonlAppender
from src.live.hub import LiveHub

logger = logging.getLogger(__name__)

HINTS_FILE = "hints.jsonl"
PROBLEM_TEXT = {
    "no_hub": "No hub configured (copilot.hub_url)",
    "hub_down": "Hub down",
    "transcribe_error": "Transcription failed",
    "hint_timeout": "Hint timed out",
    "hint_error": "Hint failed",
    "summary_failed": "Summary failed",
    "hint_capped": "Hint cap reached",
    "summary_capped": "Summary cap reached",
}
# A cap reached is the copilot holding back on purpose, not something broken: the card shows it as a warning.
WARN_PROBLEMS = ("hint_capped", "summary_capped")


@dataclass
class Run:
    """One stretch of Copilot on: its queues, devices and files. Its workers end when it stops."""

    session_id: str
    language: str
    local_only: bool
    pcm: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1200))
    chunks: queue.Queue = field(default_factory=queue.Queue)
    hints: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    summaries: queue.Queue = field(default_factory=queue.Queue)
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: dict[int, float] = field(default_factory=dict)  # chunk seq → when its audio ended
    sources: list[Any] = field(default_factory=list)
    chunkers: dict[str, Chunker] = field(default_factory=dict)
    recorders: dict[str, WavRecorder] = field(default_factory=dict)
    seq: int = 0
    dropped: int = 0
    levels: dict[str, tuple[float, float]] = field(default_factory=dict)  # speaker → (dBFS of the last block, when)


def _fresh_stats() -> dict[str, int]:
    return {"chunks": 0, "lines": 0, "hints": 0, "summaries": 0, "capped": 0}


@dataclass
class Status:
    """The problems and counts on the presenter's card, which every pipeline reports into."""

    push: Callable[[], None]
    problems: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=_fresh_stats)

    def reset_stats(self) -> None:
        self.stats = _fresh_stats()

    def clear(self, *codes: str) -> None:
        for code in codes:
            self.problems.pop(code, None)

    def capped(self, code: str, limit: RateLimit, per_hour: int, what: str) -> bool:
        """Count one call of this kind, or say why not: True when the hourly cap holds it back."""
        if limit.take(per_hour):
            self.clear(code)
            return False
        self.stats["capped"] += 1
        if code not in self.problems:
            logger.warning("⚠️ copilot: %s cap reached (%d per hour) — none asked for %.0f min",
                           what, per_hour, limit.retry_in() / 60)
        self.problems[code] = (f"{PROBLEM_TEXT[code]} — {per_hour} {what} calls in the last hour; "
                               f"the next in {max(1, round(limit.retry_in() / 60))} min")
        self.push()
        return True


@dataclass
class CopilotContext:
    live: LiveHub
    config: Callable[[], CopilotConfig]
    status: Status
    run: Callable[[], Optional[Run]]  # the stretch of Copilot on right now, None while it is off
    persona: Callable[[], tuple[str, str]]  # (mode, interviewee), which shape the prompts
    hints_log: JsonlAppender  # every model call and drift hint, from both pipelines

    def on_loop(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run ``fn`` on the server's loop (directly when there is none: unit tests)."""
        if self.live.loop is None:
            fn(*args)
        else:
            self.live.loop.call_soon_threadsafe(fn, *args)

    def folder(self) -> Optional[Path]:
        """The live session's ``live/copilot`` folder."""
        return self.live.folder / "live" / "copilot" if self.live.folder is not None else None

    def models(self, run: Run) -> list[str]:
        cfg = self.config()
        return model_chain(cfg.hint_models, cfg.local_model, run.local_only)

    def log_call(self, folder: Path, record: dict[str, Any]) -> None:
        self.hints_log.append(folder / HINTS_FILE, [record])

    def report_write(self, name: str, exc: Optional[OSError]) -> None:
        """A file write failed (or, with no ``exc``, worked again): the live session says so."""
        if exc is not None:
            self.live.write_failed(name, exc)
        elif not self.live.write_ok(name):
            return
        self.live.push_state()
