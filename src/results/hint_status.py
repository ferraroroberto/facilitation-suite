"""A copilot hint's outcome, declared once: shown, empty, stale or failed.

``hints.jsonl`` holds one record per hint request, written once when the answer came back (or did
not). ``hint_status`` is the one function that reads such a record and says which of the four
outcomes it is; the Conversation read-back (``src/results/conversation.py``) sends the derived
``failed`` and ``stale`` flags beside ``status``, so the card never compares the words itself.
The outcome is final: a recorded hint is never rewritten, so ``TRANSITIONS`` lists no move and
``advance`` refuses every one. Outcome table and readers: ``docs/results.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class HintStatus(StrEnum):
    """The closed set of outcomes. Adding one means handling it in ``hint_status`` and every reader."""

    SHOWN = "shown"
    EMPTY = "empty"
    STALE = "stale"
    FAILED = "failed"


MEANINGS: dict[HintStatus, str] = {
    HintStatus.SHOWN: "the model answered and the presenter saw at least one hint",
    HintStatus.EMPTY: "the model answered with nothing to suggest",
    HintStatus.STALE: "the answer came back after the item changed and was dropped",
    HintStatus.FAILED: "no answer reached the presenter (the hub was down or timed out)",
}

#: A hint record is written once, so no outcome moves to another.
TRANSITIONS: dict[HintStatus, frozenset[HintStatus]] = {status: frozenset() for status in HintStatus}


class IllegalHintTransition(ValueError):
    """Any move: a recorded hint's outcome is final."""


def advance(current: HintStatus, to: HintStatus) -> HintStatus:
    """``to`` when ``current`` may move there; no outcome may, so this always raises."""
    if to not in TRANSITIONS[current]:
        raise IllegalHintTransition(f"a {current.value} hint cannot become {to.value}")
    return to


def hint_status(rec: Mapping[str, Any]) -> HintStatus:
    """The outcome a hint record says: a failure wins over a drop, a drop over the hints it carried."""
    if rec.get("failure"):
        return HintStatus.FAILED
    if rec.get("dropped"):
        return HintStatus.STALE
    return HintStatus.SHOWN if rec.get("hints") else HintStatus.EMPTY


def is_failed(status: HintStatus) -> bool:
    """True when no answer reached the presenter."""
    return status is HintStatus.FAILED


def is_stale(status: HintStatus) -> bool:
    """True when the answer arrived after its item had passed."""
    return status is HintStatus.STALE
