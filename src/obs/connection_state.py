"""The OBS connection's state, declared once: off, connecting, connected or disconnected.

``ObsService.state`` holds one ``ObsState`` and moves only through ``advance``, which refuses a move
the table below does not list. Every reader (the presenter chip, the stage's click-to-restore, the
Settings card, the readiness check, the Test connection wait) asks ``is_connected`` /
``is_connecting`` / ``is_off`` or reads the derived ``connected`` / ``connecting`` / ``off`` fields
of the snapshot instead of spelling the states. State table and readers: ``docs/obs.md``.
"""

from __future__ import annotations

from enum import StrEnum


class ObsState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    OFF = "off"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


MEANINGS: dict[ObsState, str] = {
    ObsState.OFF: "scene switching is turned off in Settings; no connection is held",
    ObsState.CONNECTING: "an attempt is running now (the first one after start, a retry, or a reconnect on request)",
    ObsState.CONNECTED: "the last request or heartbeat worked",
    ObsState.DISCONNECTED: "OBS is not reachable (closed, its WebSocket server is off, or the link was lost); a retry follows",
}

#: Each state and the states it may move to. A move to the same state is not a transition (only the detail changes).
#: ``OFF`` and ``DISCONNECTED`` always go through ``CONNECTING``: no state is ``CONNECTED`` without an attempt that worked.
TRANSITIONS: dict[ObsState, frozenset[ObsState]] = {
    ObsState.OFF: frozenset({ObsState.CONNECTING}),
    ObsState.CONNECTING: frozenset({ObsState.CONNECTED, ObsState.DISCONNECTED, ObsState.OFF}),
    ObsState.CONNECTED: frozenset({ObsState.CONNECTING, ObsState.DISCONNECTED, ObsState.OFF}),
    ObsState.DISCONNECTED: frozenset({ObsState.CONNECTING, ObsState.OFF}),
}


class IllegalObsTransition(ValueError):
    """A move the table does not list (for example OBS connected without an attempt)."""


def advance(current: ObsState, to: ObsState) -> ObsState:
    """``to`` when ``current`` may move there, otherwise ``IllegalObsTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalObsTransition(f"OBS connection cannot go from {current.value} to {to.value}")
    return to


def is_connected(state: ObsState) -> bool:
    """True when a scene switch can be sent now."""
    return state is ObsState.CONNECTED


def is_connecting(state: ObsState) -> bool:
    """True while an attempt is running."""
    return state is ObsState.CONNECTING


def is_off(state: ObsState) -> bool:
    """True when switching is turned off, so no attempt is coming."""
    return state is ObsState.OFF
