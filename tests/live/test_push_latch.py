"""The coalesced push's latch: a closed state set, a transition table, and one push per burst."""

from __future__ import annotations

import itertools
from typing import Any, Callable

import pytest

from src.live.push_latch import (
    MEANINGS,
    PUSH_EVERY_S,
    TRANSITIONS,
    IllegalPushTransition,
    PushLatch,
    PushState,
    advance,
)


class FakeLoop:
    """Records ``call_later`` and runs what is due on demand, like the live event loop would."""

    def __init__(self) -> None:
        self.due: list[tuple[float, Callable[[], None]]] = []

    def call_later(self, delay: float, fn: Callable[[], None]) -> None:
        self.due.append((delay, fn))

    def run_due(self) -> None:
        due, self.due = self.due, []
        for _, fn in due:
            fn()


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in PushState} == {"idle", "scheduled"}
    assert set(MEANINGS) == set(PushState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: push-latch
@pytest.mark.parametrize("current,to", [
    (PushState.IDLE, PushState.IDLE),
    (PushState.SCHEDULED, PushState.SCHEDULED),  # a second push scheduled while one waits
])
def test_an_illegal_move_is_refused(current: PushState, to: PushState) -> None:
    with pytest.raises(IllegalPushTransition):
        advance(current, to)


def test_every_listed_move_is_allowed_and_nothing_else() -> None:
    for current, to in itertools.product(PushState, repeat=2):
        if to in TRANSITIONS[current]:
            assert advance(current, to) is to
        else:
            with pytest.raises(IllegalPushTransition):
                advance(current, to)


def test_a_burst_schedules_one_push_and_the_next_burst_another() -> None:
    loop, pushed = FakeLoop(), []
    latch = PushLatch()
    assert latch.state is PushState.IDLE and not latch.is_scheduled()
    assert [latch.push_soon(loop, lambda: pushed.append(1)) for _ in range(5)] == [True, False, False, False, False]
    assert latch.state is PushState.SCHEDULED and [d for d, _ in loop.due] == [PUSH_EVERY_S]
    loop.run_due()
    assert pushed == [1] and latch.state is PushState.IDLE
    assert latch.push_soon(loop, lambda: pushed.append(2)) is True
    loop.run_due()
    assert pushed == [1, 2]


def test_a_loop_that_refuses_the_timer_leaves_the_latch_idle() -> None:
    class Closed:
        def call_later(self, *_: Any) -> None:
            raise RuntimeError("Event loop is closed")

    latch = PushLatch()
    with pytest.raises(RuntimeError):
        latch.push_soon(Closed(), lambda: None)  # type: ignore[arg-type]
    assert latch.state is PushState.IDLE
