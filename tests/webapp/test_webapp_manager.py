"""``WebappManager``: the wedged state (port open, ``/healthz`` silent) must stay distinct
from both running and not-running — never adopted as healthy by ``status()`` or ``start()``,
and still terminated by ``stop()``/``restart()`` when it is a process this manager owns."""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from app.webapp.manager import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    WebappManager,
    WebappManagerConfig,
)
from src.no_window import NO_WINDOW


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def manager() -> WebappManager:
    return WebappManager(WebappManagerConfig(host="127.0.0.1", port=_free_port()))


@pytest.fixture
def owned_process():
    """A real, long-lived child process standing in for a spawned-but-wedged webapp —
    never bound to the manager's port, so the port-open/reachable signals stay fully
    mocked and deterministic."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], creationflags=NO_WINDOW
    )
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_status_not_running_when_port_closed(manager: WebappManager) -> None:
    status = manager.status()
    assert status == manager.status()  # stable across calls
    assert not status.running and not status.wedged
    assert status.ownership == OWNERSHIP_NONE


def test_status_wedged_external_when_port_open_but_healthz_silent(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "is_reachable", lambda: False)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: True)
    status = manager.status()
    assert status.wedged and not status.running
    assert status.ownership == OWNERSHIP_EXTERNAL
    assert status.pid is None


def test_status_wedged_ours_when_we_own_the_process(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch, owned_process
) -> None:
    manager._proc = owned_process
    monkeypatch.setattr(manager, "is_reachable", lambda: False)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: True)
    status = manager.status()
    assert status.wedged and not status.running
    assert status.ownership == OWNERSHIP_OURS
    assert status.pid == owned_process.pid


def test_status_running_when_healthz_answers(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "is_reachable", lambda: True)
    status = manager.status()
    assert status.running and not status.wedged
    assert status.ownership == OWNERSHIP_EXTERNAL


def test_start_refuses_to_adopt_a_wedged_webapp(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "is_reachable", lambda: False)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: True)
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(1))
    with pytest.raises(RuntimeError, match="wedged"):
        manager.start()
    assert not spawned  # never silently adopted, and never spawns a second uvicorn on top


def test_stop_terminates_a_wedged_process_we_own(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch, owned_process
) -> None:
    manager._proc = owned_process
    monkeypatch.setattr(manager, "is_reachable", lambda: False)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: True)
    status = manager.stop()
    assert not status.running and not status.wedged
    assert status.ownership == OWNERSHIP_NONE
    owned_process.wait(timeout=5)
    assert owned_process.poll() is not None  # actually terminated, not left running


def test_restart_stops_a_wedged_process_before_starting_fresh(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch, owned_process
) -> None:
    manager._proc = owned_process
    reachable = {"value": False}
    # Tied to the real process, not a free-standing flag: once stop() actually
    # terminates `owned_process`, the port genuinely frees up.
    monkeypatch.setattr(manager, "is_reachable", lambda: reachable["value"])
    monkeypatch.setattr(manager, "is_port_in_use", lambda: owned_process.poll() is None)

    real_popen = subprocess.Popen

    def _fake_popen(*_args: object, **_kwargs: object) -> subprocess.Popen:
        reachable["value"] = True  # the fresh process answers /healthz at once
        return real_popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], creationflags=NO_WINDOW
        )

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(manager, "_wait_until_ready", lambda: None)
    new_status = manager.restart()
    owned_process.wait(timeout=5)
    assert owned_process.poll() is not None  # the wedged original was terminated
    assert new_status.running and new_status.ownership == OWNERSHIP_OURS
    manager._proc.kill()
    manager._proc.wait(timeout=5)


def test_restart_refuses_a_wedged_external_process(
    manager: WebappManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "is_reachable", lambda: False)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: True)
    with pytest.raises(RuntimeError, match="externally"):
        manager.restart()
