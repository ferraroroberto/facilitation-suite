"""The copilot channel state: a closed set, a transition table, and the flags derived from it."""

from __future__ import annotations

import pytest

from src.copilot.channel_state import (
    MEANINGS,
    TRANSITIONS,
    ChannelState,
    IllegalChannelTransition,
    advance,
    is_failed,
    is_recording,
    is_starting,
)


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in ChannelState} == {"starting", "recording", "error"}
    assert set(MEANINGS) == set(ChannelState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: copilot-channel
@pytest.mark.parametrize("current,to", [
    (ChannelState.STARTING, ChannelState.STARTING),    # a channel is opened once
    (ChannelState.RECORDING, ChannelState.STARTING),   # an open device is not reopened by the same run
    (ChannelState.RECORDING, ChannelState.ERROR),      # a capturing channel does not turn into a failed one
    (ChannelState.ERROR, ChannelState.RECORDING),      # a device that failed to open does not start capturing
])
def test_an_illegal_move_is_refused(current: ChannelState, to: ChannelState) -> None:
    with pytest.raises(IllegalChannelTransition):
        advance(current, to)


@pytest.mark.parametrize("current,to", [
    (ChannelState.STARTING, ChannelState.RECORDING),
    (ChannelState.STARTING, ChannelState.ERROR),
])
def test_a_listed_move_is_allowed(current: ChannelState, to: ChannelState) -> None:
    assert advance(current, to) is to


def test_the_derived_flags_per_state() -> None:
    assert {s: (is_starting(s), is_recording(s), is_failed(s)) for s in ChannelState} == {
        ChannelState.STARTING: (True, False, False),
        ChannelState.RECORDING: (False, True, False),
        ChannelState.ERROR: (False, False, True),
    }
