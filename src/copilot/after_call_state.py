"""The after-call run's state, declared once: running, done or failed.

``Job.state`` holds one ``AfterCallState`` and moves only through ``advance``, which refuses a move
the table below does not list. The single-run guard and the job's wire form (``running``,
``failed``) read the state through ``is_running`` / ``is_failed``; the Results tab's recorder
section reads those two fields instead of spelling the states. State table and readers:
``docs/copilot.md``.
"""

from __future__ import annotations

from enum import StrEnum


class AfterCallState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


MEANINGS: dict[AfterCallState, str] = {
    AfterCallState.RUNNING: "transcribing the takes, or summarising the transcript already on disk",
    AfterCallState.DONE: "finished; a transcript stands, and the summary may have failed on its own",
    AfterCallState.FAILED: "stopped before a transcript was written; the reason is in the failure and detail",
}

#: Each state and the states it may move to; ``DONE`` and ``FAILED`` are final (a new run starts a new job).
TRANSITIONS: dict[AfterCallState, frozenset[AfterCallState]] = {
    AfterCallState.RUNNING: frozenset({AfterCallState.DONE, AfterCallState.FAILED}),
    AfterCallState.DONE: frozenset(),
    AfterCallState.FAILED: frozenset(),
}


class IllegalAfterCallTransition(ValueError):
    """A move the table does not list (for example a finished run running again)."""


def advance(current: AfterCallState, to: AfterCallState) -> AfterCallState:
    """``to`` when ``current`` may move there, otherwise ``IllegalAfterCallTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalAfterCallTransition(f"after-call run cannot go from {current.value} to {to.value}")
    return to


def is_running(state: AfterCallState) -> bool:
    """True while the run holds the session's single slot and the UI keeps polling."""
    return state is AfterCallState.RUNNING


def is_failed(state: AfterCallState) -> bool:
    """True when no transcript was written."""
    return state is AfterCallState.FAILED
