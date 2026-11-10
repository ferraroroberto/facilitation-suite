"""The deck-import job's state, declared once: queued, running, done or error.

``ImportJob.state`` holds one ``ImportState`` and moves only through ``advance``, which refuses a
move the table below does not list. The service's busy guard and the job's wire form
(``active``, ``succeeded``) read the state through ``is_active`` / ``is_succeeded``; the import
dialog reads those two fields instead of spelling the states. State table and readers:
``docs/import.md``.
"""

from __future__ import annotations

from enum import StrEnum


class ImportState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


MEANINGS: dict[ImportState, str] = {
    ImportState.QUEUED: "accepted, the worker thread has not started the export",
    ImportState.RUNNING: "exporting and analysing slides",
    ImportState.DONE: "finished; the result is on the job",
    ImportState.ERROR: "stopped with a code and a message; nothing was applied",
}

#: Each state and the states it may move to; ``DONE`` and ``ERROR`` are final.
TRANSITIONS: dict[ImportState, frozenset[ImportState]] = {
    ImportState.QUEUED: frozenset({ImportState.RUNNING}),
    ImportState.RUNNING: frozenset({ImportState.DONE, ImportState.ERROR}),
    ImportState.DONE: frozenset(),
    ImportState.ERROR: frozenset(),
}


class IllegalImportTransition(ValueError):
    """A move the table does not list (for example a finished job running again)."""


def advance(current: ImportState, to: ImportState) -> ImportState:
    """``to`` when ``current`` may move there, otherwise ``IllegalImportTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalImportTransition(f"import job cannot go from {current.value} to {to.value}")
    return to


def is_active(state: ImportState) -> bool:
    """True while the job still holds the single import slot and the UI keeps polling."""
    return state in (ImportState.QUEUED, ImportState.RUNNING)


def is_succeeded(state: ImportState) -> bool:
    return state is ImportState.DONE
