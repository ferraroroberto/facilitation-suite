"""The music player's state, declared once: idle, playing, paused or error.

``MusicService.state`` holds one ``PlayerState`` and moves only through ``advance``, which refuses
a move the table below does not list. The service's guards and the snapshot's derived flags
(``playing``, ``paused``, ``loaded``, ``engaged``) read the state through the helpers here; the
presenter and the phone remote read those flags instead of spelling the states. State table and
readers: ``docs/music.md``.
"""

from __future__ import annotations

from enum import StrEnum


class PlayerState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


MEANINGS: dict[PlayerState, str] = {
    PlayerState.IDLE: "nothing loaded: never started, stopped, or the track ended",
    PlayerState.PLAYING: "a track is loaded and its clock runs",
    PlayerState.PAUSED: "a track is loaded and held; resume continues from where it stopped",
    PlayerState.ERROR: "a start or a backend failed; the reason is in the detail",
}

#: Each state and the states it may move to. A state moves to itself where that happens for real:
#: a stop with nothing playing, a new track replacing the playing one, a second failure.
TRANSITIONS: dict[PlayerState, frozenset[PlayerState]] = {
    PlayerState.IDLE: frozenset({PlayerState.IDLE, PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.ERROR}),
    PlayerState.PLAYING: frozenset({PlayerState.IDLE, PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.ERROR}),
    PlayerState.PAUSED: frozenset({PlayerState.IDLE, PlayerState.PLAYING, PlayerState.ERROR}),
    PlayerState.ERROR: frozenset({PlayerState.IDLE, PlayerState.PLAYING, PlayerState.ERROR}),
}


class IllegalPlayerTransition(ValueError):
    """A move the table does not list (for example an error going straight to paused)."""


def advance(current: PlayerState, to: PlayerState) -> PlayerState:
    """``to`` when ``current`` may move there, otherwise ``IllegalPlayerTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalPlayerTransition(f"music player cannot go from {current.value} to {to.value}")
    return to


def is_loaded(state: PlayerState) -> bool:
    """True while a track is loaded, playing or held."""
    return state in (PlayerState.PLAYING, PlayerState.PAUSED)


def is_engaged(state: PlayerState) -> bool:
    """True unless idle: the music card has something to show (a track, or why it failed)."""
    return state is not PlayerState.IDLE
