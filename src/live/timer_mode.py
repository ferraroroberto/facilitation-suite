"""The item timer's mode, declared once: idle, running, paused or done.

An item's timer is a ``TimerState`` in ``LiveHub.timers``; an item that never started one has no
entry. ``timer_mode`` is the only code that maps those facts to a mode. The hub's guards, the
music restore and the snapshot's ``mode`` field all call it, and the JS views read that field
through ``timerMode`` in ``live.js``; none of them re-derive the mode from ``running_since`` or
``done``. State table and readers: ``docs/timer-state.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TimerMode(str, Enum):
    """The closed set of modes. Adding one means handling it in every reader."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"


MEANINGS: dict[TimerMode, str] = {
    TimerMode.IDLE: "no timer entry yet: never started, or reset",
    TimerMode.RUNNING: "counting down; the end is scheduled",
    TimerMode.PAUSED: "started and stopped with time left",
    TimerMode.DONE: "reached 00:00; nothing is counting",
}


class InconsistentTimer(ValueError):
    """A timer whose fields describe no mode (done and counting at once, or a negative length)."""


@dataclass
class TimerState:
    total: int
    elapsed: float = 0.0
    running_since: Optional[int] = None
    done: bool = False

    def remaining(self, at_ms: int) -> float:
        running = (at_ms - self.running_since) / 1000 if self.running_since is not None else 0.0
        return self.total - self.elapsed - running

    def as_dict(self) -> dict[str, Any]:
        """The snapshot and ``live/state.json`` form: the stored fields (older clients and files read
        those) plus the mode derived from them."""
        return {"total": self.total, "elapsed": round(self.elapsed, 3), "running_since": self.running_since,
                "done": self.done, "mode": timer_mode(self).value}


def timer_mode(t: Optional[TimerState]) -> TimerMode:
    """The mode of one observation: the item's timer, or ``None`` when it has no entry.

    Refuses an impossible combination instead of picking a neighbour for it.
    """
    if t is None:
        return TimerMode.IDLE
    if t.total < 0:
        raise InconsistentTimer(f"timer has a negative length ({t.total})")
    if t.done:
        if t.running_since is not None:
            raise InconsistentTimer("timer is done and still counting")
        return TimerMode.DONE
    return TimerMode.PAUSED if t.running_since is None else TimerMode.RUNNING


def restore_timer(item_id: str, raw: dict[str, Any]) -> Optional[TimerState]:
    """A timer read back from ``live/state.json``, normalised if the file holds an impossible one.

    A done timer that is still counting comes back done (the end won). A negative length cannot be
    resumed, so the timer is dropped and the item starts from its configured length. Both are logged.
    """
    t = TimerState(total=int(raw.get("total", 0)), elapsed=float(raw.get("elapsed", 0)),
                   running_since=raw.get("running_since"), done=bool(raw.get("done")))
    if t.total < 0:
        logger.info("ℹ️ live: timer of %s restored with a negative length (%s) — dropped", item_id, t.total)
        return None
    if t.done and t.running_since is not None:
        logger.info("ℹ️ live: timer of %s restored done and counting — kept as done", item_id)
        t.elapsed, t.running_since = float(t.total), None
    return t
