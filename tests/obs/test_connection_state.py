"""The OBS connection state: a closed set, a transition table, and the wire fields derived from it."""

from __future__ import annotations

import itertools

import pytest

from src.obs.connection_state import (
    MEANINGS,
    TRANSITIONS,
    IllegalObsTransition,
    ObsState,
    advance,
    is_connected,
    is_connecting,
    is_off,
)
from src.obs.service import ObsService


def _service(state: ObsState) -> ObsService:
    svc = ObsService(lambda: None)  # type: ignore[arg-type,return-value]  # the config is read only by the worker
    svc.state = state
    return svc


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in ObsState} == {"off", "connecting", "connected", "disconnected"}
    assert set(MEANINGS) == set(ObsState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: obs-connection
@pytest.mark.parametrize("current,to", [
    (ObsState.OFF, ObsState.CONNECTED),            # connected without an attempt
    (ObsState.OFF, ObsState.DISCONNECTED),         # a lost link that was never made
    (ObsState.DISCONNECTED, ObsState.CONNECTED),   # a retry that skips the attempt
    (ObsState.CONNECTING, ObsState.CONNECTING),
    (ObsState.CONNECTED, ObsState.CONNECTED),
])
def test_an_illegal_move_is_refused(current: ObsState, to: ObsState) -> None:
    with pytest.raises(IllegalObsTransition):
        advance(current, to)
    svc = _service(current)
    if current is not to:  # staying put with a new detail is not a move
        with pytest.raises(IllegalObsTransition):
            svc._set(to, "x")
        assert svc.state is current


def test_every_listed_move_is_allowed_and_nothing_else() -> None:
    for current, to in itertools.permutations(ObsState, 2):
        if to in TRANSITIONS[current]:
            assert advance(current, to) is to
        else:
            with pytest.raises(IllegalObsTransition):
                advance(current, to)


def test_staying_in_a_state_updates_only_the_detail() -> None:
    svc = _service(ObsState.DISCONNECTED)
    svc._set(ObsState.DISCONNECTED, "Lost OBS again")
    assert (svc.state, svc.detail) == (ObsState.DISCONNECTED, "Lost OBS again")


def test_the_snapshot_keeps_state_and_derives_connected_connecting_and_off() -> None:
    rows = {s: _service(s).snapshot() for s in ObsState}
    assert {s: (r["state"], r["connected"], r["connecting"], r["off"]) for s, r in rows.items()} == {
        ObsState.OFF: ("off", False, False, True),
        ObsState.CONNECTING: ("connecting", False, True, False),
        ObsState.CONNECTED: ("connected", True, False, False),
        ObsState.DISCONNECTED: ("disconnected", False, False, False),
    }
    assert all(is_connected(s) == r["connected"] and is_connecting(s) == r["connecting"] and is_off(s) == r["off"]
               for s, r in rows.items())
