"""``LiveHub.push_state_threadsafe``: the one worker-thread hop for a fresh snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.live.hub import LiveHub


class FakeLoop:
    def __init__(self) -> None:
        self.hops: list[Callable[..., Any]] = []

    def call_soon_threadsafe(self, fn: Callable[..., Any], *args: Any) -> None:
        self.hops.append(fn)


def test_before_the_loop_exists_it_does_nothing() -> None:
    hub = object.__new__(LiveHub)
    hub.loop = None
    hub.push_state_threadsafe()  # must not raise


def test_it_hops_onto_the_loop_with_push_state() -> None:
    hub = object.__new__(LiveHub)
    hub.loop = FakeLoop()
    hub.push_state_threadsafe()
    assert hub.loop.hops == [hub.push_state]
