"""The tray's watchdog handler must never stack a second webapp copy on a slow boot.

A fake process table and a fake clock stand in for real processes: the manager
"spawns" a pid into the table, the guard reads liveness from it, and the clock
advances by hand. No process is started and nothing sleeps.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tray import tray as tray_mod
from app.tray.watchdog import DEFAULT_STARTUP_WINDOW_S, RespawnGuard
from src.config import AppConfig


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeProcessTable:
    """Live pids and the stops asked of them."""

    def __init__(self) -> None:
        self.alive: set[int] = set()
        self.stopped: list[int] = []

    def identity(self, pid: int) -> int | None:
        return pid if pid in self.alive else None

    def stop_tree(self, pid: int) -> None:
        self.stopped.append(pid)
        self.alive.discard(pid)


class FakeManager:
    """Never listening and never answering, like a webapp still importing."""

    base_url = "https://127.0.0.1:0"
    public_url = base_url

    def __init__(self, table: FakeProcessTable) -> None:
        self._table = table
        self.spawned: list[int] = []
        self.child_pid: int | None = None

    def is_reachable(self) -> bool:
        return False

    def is_port_in_use(self) -> bool:
        return False

    def start(self, wait: bool = True) -> SimpleNamespace:
        pid = 5000 + len(self.spawned)
        self.spawned.append(pid)
        self._table.alive.add(pid)
        self.child_pid = pid
        return SimpleNamespace(pid=None, running=False)


class FakeTunnel:
    def __init__(self, *args, **kwargs) -> None:
        pass


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    table = FakeProcessTable()
    clock = FakeClock()
    manager = FakeManager(table)
    monkeypatch.setattr(tray_mod, "WebappManager", lambda cfg: manager)
    monkeypatch.setattr(tray_mod, "Tunnel", FakeTunnel)
    monkeypatch.setattr(tray_mod, "WATCHDOG_LOG", tmp_path / "watchdog.log")
    monkeypatch.setattr(tray_mod, "_notify", lambda title, message: None)
    app = tray_mod.TrayApp(AppConfig(), instance=SimpleNamespace())
    app.guard = RespawnGuard(
        app.wd_log, identity=table.identity, stop_tree=table.stop_tree, clock=clock
    )
    return SimpleNamespace(app=app, manager=manager, table=table, clock=clock)


def test_second_respawn_is_refused_while_the_first_child_is_still_starting(world) -> None:
    world.app._on_wedge(3)
    world.clock.now += 60  # next probe tick: the child is still importing
    world.app._on_wedge(3)
    world.clock.now += 60
    world.app._on_wedge(3)

    assert world.manager.spawned == [5000]
    assert world.table.alive == {5000}
    assert world.table.stopped == []


def test_a_child_that_never_got_healthy_is_stopped_before_the_next_spawn(world) -> None:
    world.app._on_wedge(3)
    world.clock.now += DEFAULT_STARTUP_WINDOW_S + 1
    world.app._on_wedge(3)

    assert world.table.stopped == [5000]
    assert world.manager.spawned == [5000, 5001]
    assert world.table.alive == {5001}


def test_a_child_that_already_exited_is_replaced_without_a_stop(world) -> None:
    world.app._on_wedge(3)
    world.table.alive.clear()  # it crashed
    world.app._on_wedge(3)

    assert world.table.stopped == []
    assert world.manager.spawned == [5000, 5001]
