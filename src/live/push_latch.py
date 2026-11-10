"""The coalesced state push's latch, declared once: idle or scheduled.

A burst of chat messages, joins or answers would push a full snapshot to every view per event.
``PushLatch`` lets the first event schedule one push ``PUSH_EVERY_S`` later and drops the events
that arrive while it waits. It moves only through ``advance``, which refuses a move the table below
does not list, so a push can never be scheduled twice. State table and readers: ``docs/live.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum

PUSH_EVERY_S = 0.25  # at most four pushes a second during a burst


class PushState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and ``push_soon``."""

    IDLE = "idle"
    SCHEDULED = "scheduled"


MEANINGS: dict[PushState, str] = {
    PushState.IDLE: "no push is waiting; the next event schedules one",
    PushState.SCHEDULED: "a push is due within PUSH_EVERY_S; events that arrive now ride on it",
}

#: Each state and the one it may move to. Staying put is not a transition: ``push_soon`` returns early instead.
TRANSITIONS: dict[PushState, frozenset[PushState]] = {
    PushState.IDLE: frozenset({PushState.SCHEDULED}),
    PushState.SCHEDULED: frozenset({PushState.IDLE}),
}


class IllegalPushTransition(ValueError):
    """A move the table does not list (for example scheduling a push while one is already waiting)."""


def advance(current: PushState, to: PushState) -> PushState:
    """``to`` when ``current`` may move there, otherwise ``IllegalPushTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalPushTransition(f"push latch cannot go from {current.value} to {to.value}")
    return to


class PushLatch:
    """One pending push at a time, on the live event loop."""

    def __init__(self) -> None:
        self.state = PushState.IDLE

    def is_scheduled(self) -> bool:
        """True while a push is waiting, so a new event needs no push of its own."""
        return self.state is PushState.SCHEDULED

    def push_soon(self, loop: asyncio.AbstractEventLoop, push: Callable[[], None]) -> bool:
        """Run ``push`` once within ``PUSH_EVERY_S``; False when one is already waiting."""
        if self.is_scheduled():
            return False
        self.state = advance(self.state, PushState.SCHEDULED)

        def fire() -> None:
            self.state = advance(self.state, PushState.IDLE)
            push()

        try:
            loop.call_later(PUSH_EVERY_S, fire)
        except BaseException:
            self.state = advance(self.state, PushState.IDLE)  # nothing was scheduled, so nothing is waiting
            raise
        return True
