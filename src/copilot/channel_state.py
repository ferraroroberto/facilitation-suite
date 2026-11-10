"""A copilot capture channel's state, declared once: starting, recording or error.

``CopilotService.channels[speaker]["state"]`` holds one ``ChannelState`` and moves only through
``advance``, which refuses a move the table below does not list. The presenter's card and header
chip read the snapshot's derived flags (``starting``, ``recording``, ``failed``) instead of
spelling the states (``state`` itself is still sent). State table and readers: ``docs/copilot.md``.
"""

from __future__ import annotations

from enum import StrEnum


class ChannelState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    STARTING = "starting"
    RECORDING = "recording"
    ERROR = "error"


MEANINGS: dict[ChannelState, str] = {
    ChannelState.STARTING: "the device is being opened",
    ChannelState.RECORDING: "the device is open and its audio is being captured",
    ChannelState.ERROR: "the device could not be opened; the reason is in the detail",
}

#: Each state and the states it may move to. A run opens each device once, so ``recording`` and
#: ``error`` end the channel's story; switching Copilot off and on plans fresh channels.
TRANSITIONS: dict[ChannelState, frozenset[ChannelState]] = {
    ChannelState.STARTING: frozenset({ChannelState.RECORDING, ChannelState.ERROR}),
    ChannelState.RECORDING: frozenset(),
    ChannelState.ERROR: frozenset(),
}


class IllegalChannelTransition(ValueError):
    """A move the table does not list (for example a channel that failed to open then recording)."""


def advance(current: ChannelState, to: ChannelState) -> ChannelState:
    """``to`` when ``current`` may move there, otherwise ``IllegalChannelTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalChannelTransition(f"copilot channel cannot go from {current.value} to {to.value}")
    return to


def is_starting(state: ChannelState) -> bool:
    """True while the device is being opened."""
    return state is ChannelState.STARTING


def is_recording(state: ChannelState) -> bool:
    """True once the device is capturing."""
    return state is ChannelState.RECORDING


def is_failed(state: ChannelState) -> bool:
    """True when the device could not be opened."""
    return state is ChannelState.ERROR
