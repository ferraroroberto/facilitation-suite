"""The Spotify login state: a closed set, a transition table, and the flags derived from it."""

from __future__ import annotations

import pytest

from src.music.login_state import (
    MEANINGS,
    TRANSITIONS,
    IllegalLoginTransition,
    LoginState,
    advance,
    is_done,
    is_failed,
    is_waiting,
)


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in LoginState} == {"idle", "waiting", "done", "failed"}
    assert set(MEANINGS) == set(LoginState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: spotify-login
@pytest.mark.parametrize("current,to", [
    (LoginState.IDLE, LoginState.DONE),        # a login that never started cannot finish
    (LoginState.DONE, LoginState.FAILED),      # a saved token is not undone by a late failure
    (LoginState.FAILED, LoginState.DONE),      # a failed login does not turn into a saved one
    (LoginState.WAITING, LoginState.IDLE),     # a started login only ends done or failed
])
def test_an_illegal_move_is_refused(current: LoginState, to: LoginState) -> None:
    with pytest.raises(IllegalLoginTransition):
        advance(current, to)


@pytest.mark.parametrize("current,to", [
    (LoginState.IDLE, LoginState.WAITING),
    (LoginState.WAITING, LoginState.WAITING),  # a new start replaces a login still waiting
    (LoginState.WAITING, LoginState.DONE),
    (LoginState.WAITING, LoginState.FAILED),
    (LoginState.DONE, LoginState.WAITING),     # Reconnect Spotify
    (LoginState.FAILED, LoginState.WAITING),   # try again
])
def test_a_listed_move_is_allowed(current: LoginState, to: LoginState) -> None:
    assert advance(current, to) is to


def test_the_derived_flags_per_state() -> None:
    assert {s: (is_waiting(s), is_done(s), is_failed(s)) for s in LoginState} == {
        LoginState.IDLE: (False, False, False),
        LoginState.WAITING: (True, False, False),
        LoginState.DONE: (False, True, False),
        LoginState.FAILED: (False, False, True),
    }
