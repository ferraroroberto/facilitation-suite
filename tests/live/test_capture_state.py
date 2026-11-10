"""The capture status: a closed set, a transition table, and the status derived from the windows."""

from __future__ import annotations

import pytest

from src.live.capture_state import (
    MEANINGS,
    TRANSITIONS,
    CaptureStatus,
    IllegalCaptureTransition,
    advance,
    capture_status,
    is_idle,
    is_live,
    is_stopped,
)


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in CaptureStatus} == {"idle", "live", "stopped"}
    assert set(MEANINGS) == set(CaptureStatus) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: capture-status
@pytest.mark.parametrize("current,to", [
    (CaptureStatus.IDLE, CaptureStatus.STOPPED),    # a capture that never started cannot be stopped
    (CaptureStatus.IDLE, CaptureStatus.IDLE),       # nothing moves idle to idle
    (CaptureStatus.LIVE, CaptureStatus.LIVE),       # a live capture is never started twice
    (CaptureStatus.LIVE, CaptureStatus.IDLE),       # a window is never taken back
    (CaptureStatus.STOPPED, CaptureStatus.IDLE),    # a stopped capture is reopened, never reset
    (CaptureStatus.STOPPED, CaptureStatus.STOPPED),  # a frozen result is not stopped again
])
def test_an_illegal_move_is_refused(current: CaptureStatus, to: CaptureStatus) -> None:
    with pytest.raises(IllegalCaptureTransition):
        advance(current, to)


@pytest.mark.parametrize("current,to", [
    (CaptureStatus.IDLE, CaptureStatus.LIVE),
    (CaptureStatus.LIVE, CaptureStatus.STOPPED),
    (CaptureStatus.STOPPED, CaptureStatus.LIVE),
])
def test_a_listed_move_is_allowed(current: CaptureStatus, to: CaptureStatus) -> None:
    assert advance(current, to) is to


@pytest.mark.parametrize("windows,expected", [
    (None, CaptureStatus.IDLE),                            # an item the capture never saw
    ([], CaptureStatus.IDLE),
    ([[1000, None]], CaptureStatus.LIVE),                  # the last window is open
    ([[1000, 2000]], CaptureStatus.STOPPED),
    ([[1000, 2000], [3000, None]], CaptureStatus.LIVE),    # reopened
    ([[1000, 2000], [3000, 4000]], CaptureStatus.STOPPED),
])
def test_the_status_is_read_from_the_last_window(windows: list[list[int | None]] | None, expected: CaptureStatus) -> None:
    assert capture_status(windows) is expected


def test_the_derived_flags_per_state() -> None:
    assert {s: (is_idle(s), is_live(s), is_stopped(s)) for s in CaptureStatus} == {
        CaptureStatus.IDLE: (True, False, False),
        CaptureStatus.LIVE: (False, True, False),
        CaptureStatus.STOPPED: (False, False, True),
    }
