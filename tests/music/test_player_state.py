"""The music player state: a closed set, a transition table, and the flags derived from it."""

from __future__ import annotations

import pytest

from src.music.player_state import (
    MEANINGS,
    TRANSITIONS,
    IllegalPlayerTransition,
    PlayerState,
    advance,
    is_engaged,
    is_loaded,
)


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in PlayerState} == {"idle", "playing", "paused", "error"}
    assert set(MEANINGS) == set(PlayerState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: music-player
@pytest.mark.parametrize("current,to", [
    (PlayerState.PAUSED, PlayerState.PAUSED),   # a pause cannot be paused again
    (PlayerState.ERROR, PlayerState.PAUSED),    # a failed start has no track to hold
])
def test_an_illegal_move_is_refused(current: PlayerState, to: PlayerState) -> None:
    with pytest.raises(IllegalPlayerTransition):
        advance(current, to)


@pytest.mark.parametrize("current,to", [
    (PlayerState.IDLE, PlayerState.PLAYING),
    (PlayerState.PLAYING, PlayerState.PAUSED),
    (PlayerState.PAUSED, PlayerState.PLAYING),
    (PlayerState.PLAYING, PlayerState.PLAYING),  # a new track replaces the playing one
    (PlayerState.IDLE, PlayerState.IDLE),        # a stop with nothing playing
    (PlayerState.PAUSED, PlayerState.IDLE),
    (PlayerState.PLAYING, PlayerState.ERROR),
    (PlayerState.ERROR, PlayerState.PLAYING),
])
def test_a_listed_move_is_allowed(current: PlayerState, to: PlayerState) -> None:
    assert advance(current, to) is to


def test_the_derived_flags_per_state() -> None:
    assert {s: (is_loaded(s), is_engaged(s)) for s in PlayerState} == {
        PlayerState.IDLE: (False, False),
        PlayerState.PLAYING: (True, True),
        PlayerState.PAUSED: (True, True),
        PlayerState.ERROR: (False, True),
    }
