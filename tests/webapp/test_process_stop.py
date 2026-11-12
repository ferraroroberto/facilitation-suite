"""``stop_process``: terminate, wait the given time, kill only if the child is still alive."""

from __future__ import annotations

import subprocess

from src.process_stop import stop_process


class FakeProc:
    def __init__(self, hangs: bool) -> None:
        self.hangs = hangs
        self.calls: list[tuple[str, float | None]] = []

    def terminate(self) -> None:
        self.calls.append(("terminate", None))

    def wait(self, timeout: float) -> int:
        self.calls.append(("wait", timeout))
        if self.hangs:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self) -> None:
        self.calls.append(("kill", None))


def test_a_child_that_exits_is_not_killed() -> None:
    proc = FakeProc(hangs=False)
    stop_process(proc, 3)  # type: ignore[arg-type]
    assert proc.calls == [("terminate", None), ("wait", 3)]


def test_a_child_that_hangs_is_killed_after_the_wait() -> None:
    proc = FakeProc(hangs=True)
    stop_process(proc, 10)  # type: ignore[arg-type]
    assert proc.calls == [("terminate", None), ("wait", 10), ("kill", None)]
