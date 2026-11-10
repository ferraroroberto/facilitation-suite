"""A capture's status, declared once: idle, live or stopped.

An activity's capture is a list of ``[start, stop)`` windows (``CaptureService.windows``);
``capture_status`` is the one function that reads that list and says which of the three states the
item is in. ``advance`` refuses a move the table below does not list; ``CaptureService`` calls it
before it opens or closes a window. The snapshot's capture block carries the derived flags
(``idle``, ``live``, ``stopped``) so the presenter and the phone remote read those instead of
spelling the states (``status`` itself is still sent, as the dot's CSS class). State table and
readers: ``docs/live.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Optional


class CaptureStatus(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    IDLE = "idle"
    LIVE = "live"
    STOPPED = "stopped"


MEANINGS: dict[CaptureStatus, str] = {
    CaptureStatus.IDLE: "no window was ever opened: answers are not counted yet",
    CaptureStatus.LIVE: "the last window is open: every participant message counts",
    CaptureStatus.STOPPED: "the last window is closed: the result is frozen; a reopen starts a new window",
}

#: Each state and the states it may move to. A stopped capture reopens as a new window; an item
#: that was never started cannot be stopped, and a live one is never "started" twice.
TRANSITIONS: dict[CaptureStatus, frozenset[CaptureStatus]] = {
    CaptureStatus.IDLE: frozenset({CaptureStatus.LIVE}),
    CaptureStatus.LIVE: frozenset({CaptureStatus.STOPPED}),
    CaptureStatus.STOPPED: frozenset({CaptureStatus.LIVE}),
}


class IllegalCaptureTransition(ValueError):
    """A move the table does not list (for example stopping a capture that never started)."""


def advance(current: CaptureStatus, to: CaptureStatus) -> CaptureStatus:
    """``to`` when ``current`` may move there, otherwise ``IllegalCaptureTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalCaptureTransition(f"capture cannot go from {current.value} to {to.value}")
    return to


def capture_status(windows: Optional[Sequence[Sequence[Optional[int]]]]) -> CaptureStatus:
    """The status an item's ``[start, stop]`` windows say: none is idle, an open last one is live."""
    if not windows:
        return CaptureStatus.IDLE
    return CaptureStatus.LIVE if windows[-1][1] is None else CaptureStatus.STOPPED


def is_idle(status: CaptureStatus) -> bool:
    """True while no window was ever opened."""
    return status is CaptureStatus.IDLE


def is_live(status: CaptureStatus) -> bool:
    """True while answers are being counted."""
    return status is CaptureStatus.LIVE


def is_stopped(status: CaptureStatus) -> bool:
    """True once the capture was closed and its result frozen."""
    return status is CaptureStatus.STOPPED
