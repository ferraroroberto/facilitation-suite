"""The after-call run state: a closed set, a transition table, and the wire fields derived from it."""

from __future__ import annotations

import pytest

from src.copilot.after_call import Job
from src.copilot.after_call_state import (
    MEANINGS,
    TRANSITIONS,
    AfterCallState,
    IllegalAfterCallTransition,
    advance,
    is_failed,
    is_running,
)


def _job(state: AfterCallState = AfterCallState.RUNNING) -> Job:
    return Job(state, "transcribe", "2026-11-10T10:00:00+00:00")


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in AfterCallState} == {"running", "done", "failed"}
    assert set(MEANINGS) == set(AfterCallState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: after-call-run
@pytest.mark.parametrize("current,to", [
    (AfterCallState.RUNNING, AfterCallState.RUNNING),
    (AfterCallState.DONE, AfterCallState.RUNNING),    # a finished run running again
    (AfterCallState.DONE, AfterCallState.FAILED),
    (AfterCallState.FAILED, AfterCallState.DONE),     # a failed run claiming it finished
    (AfterCallState.FAILED, AfterCallState.RUNNING),
])
def test_an_illegal_move_is_refused(current: AfterCallState, to: AfterCallState) -> None:
    with pytest.raises(IllegalAfterCallTransition):
        advance(current, to)


@pytest.mark.parametrize("final", [AfterCallState.DONE, AfterCallState.FAILED])
def test_a_running_job_ends_in_a_final_state(final: AfterCallState) -> None:
    assert advance(AfterCallState.RUNNING, final) is final


def test_the_wire_form_keeps_state_and_derives_running_and_failed() -> None:
    rows = {s: _job(s).as_dict() for s in AfterCallState}
    assert {s: (r["state"], r["running"], r["failed"]) for s, r in rows.items()} == {
        AfterCallState.RUNNING: ("running", True, False),
        AfterCallState.DONE: ("done", False, False),
        AfterCallState.FAILED: ("failed", False, True),
    }
    assert all(is_running(s) == r["running"] and is_failed(s) == r["failed"] for s, r in rows.items())
