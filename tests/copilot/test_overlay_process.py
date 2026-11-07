"""The overlay's process owner: it launches from the repo root, says so when the child dies at once, and
the sample endpoint tells the UI whether a live overlay took the sample."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.tray.overlay import process
from app.tray.overlay.process import OverlayProcess
from tests.conftest import SAMPLE_CONFIG, write_test_config

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the overlay is a Windows window")


def test_launch_directory_is_the_repo_root() -> None:
    # `python -m app.tray.overlay` only resolves from the folder that holds `app/`.
    assert (process.PROJECT_ROOT / "app" / "tray" / "overlay" / "__main__.py").is_file()


@windows_only
def test_a_child_that_dies_at_once_is_reported_not_called_started(caplog: pytest.LogCaptureFixture, isolated_env: Path) -> None:
    overlay = OverlayProcess(lambda: "http://127.0.0.1:1")
    overlay.module = "no_such_overlay_module"
    with caplog.at_level(logging.WARNING):
        assert overlay.start() is False
    assert not overlay.running()
    assert "No module named" in caplog.text and "exited" in caplog.text


@windows_only
def test_the_real_overlay_stays_up_when_started(isolated_env: Path) -> None:
    overlay = OverlayProcess(lambda: "http://127.0.0.1:1")  # nothing listens: the window waits and retries
    try:
        assert overlay.start() is True and overlay.running()
        assert overlay.start() is True  # one at a time
    finally:
        overlay.stop()
    assert not overlay.running()


class FakeOverlay:
    def __init__(self, up: bool) -> None:
        self.up, self.starts = up, 0

    def running(self) -> bool:
        return self.up

    def start(self) -> bool:
        self.starts += 1
        return self.up

    def stop(self) -> None:
        self.up = False


@pytest.fixture
def pc(isolated_env: Path):
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(capture="off", hint_interval_s=0)
    copilot["overlay"]["enabled"] = True
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), copilot=copilot)
    from app.webapp.server import create_app

    app = create_app()
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client, app


def test_sample_says_whether_a_live_overlay_took_it(pc) -> None:
    client, app = pc
    app.state.overlay = FakeOverlay(up=False)
    d = client.post("/api/copilot/sample").json()
    assert d["delivered"] is False and d["overlay"]["enabled"] is True and d["running"] is False
    app.state.overlay = FakeOverlay(up=True)
    d = client.post("/api/copilot/sample").json()
    assert d["delivered"] is True and d["running"] is True


def test_a_save_that_cannot_show_its_sample_says_so(pc) -> None:
    client, app = pc
    app.state.overlay = FakeOverlay(up=False)
    d = client.put("/api/copilot/settings", json={"overlay": {"position": "bottom-right"}}).json()
    assert d["delivered"] is False


def test_a_disabled_overlay_is_not_started_for_a_sample(pc) -> None:
    client, app = pc
    client.put("/api/copilot/settings", json={"overlay": {"enabled": False}})
    app.state.overlay = fake = FakeOverlay(up=False)
    d = client.post("/api/copilot/sample").json()
    assert d["delivered"] is False and fake.starts == 0
