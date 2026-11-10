"""A copilot hint's outcome: a closed set, a derive function over the logged record, and a final state."""

from __future__ import annotations

import pytest

from src.results.hint_status import (
    MEANINGS,
    TRANSITIONS,
    HintStatus,
    IllegalHintTransition,
    advance,
    hint_status,
    is_failed,
    is_stale,
)


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in HintStatus} == {"shown", "empty", "stale", "failed"}
    assert set(MEANINGS) == set(HintStatus) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: hint-outcome
@pytest.mark.parametrize("current", list(HintStatus))
@pytest.mark.parametrize("to", list(HintStatus))
def test_a_recorded_outcome_never_moves(current: HintStatus, to: HintStatus) -> None:
    with pytest.raises(IllegalHintTransition):
        advance(current, to)


@pytest.mark.parametrize("rec,expected", [
    ({"hints": [{"text": "ask why"}]}, HintStatus.SHOWN),
    ({"hints": []}, HintStatus.EMPTY),
    ({}, HintStatus.EMPTY),
    ({"dropped": "stale", "hints": [{"text": "late"}]}, HintStatus.STALE),
    ({"failure": "timeout", "hints": []}, HintStatus.FAILED),
    ({"failure": "hub_down", "dropped": "stale"}, HintStatus.FAILED),  # a failure outranks a drop
])
def test_the_outcome_is_read_from_the_record(rec: dict, expected: HintStatus) -> None:
    assert hint_status(rec) is expected


def test_the_derived_flags_per_state() -> None:
    assert {s: (is_failed(s), is_stale(s)) for s in HintStatus} == {
        HintStatus.SHOWN: (False, False),
        HintStatus.EMPTY: (False, False),
        HintStatus.STALE: (False, True),
        HintStatus.FAILED: (True, False),
    }
