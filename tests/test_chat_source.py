"""The presenter's Zoom · Teams switch (#239): the choice is saved, a running
reader restarts on it, a stopped one only saves it. No real reader starts:
``subprocess.Popen`` is a fake that records the command."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from src.chat import process, reader
from src.chat.process import ReaderProcess
from src.config import load_config
from tests.conftest import paired_remote


class _FakeProc:
    """What ``ReaderProcess`` needs of a ``Popen``: alive until waited on."""

    started: list[list[str]] = []

    def __init__(self, cmd: list[str], **_: Any) -> None:
        self.cmd, self.pid, self.alive = cmd, 4242 + len(self.started), True
        self.started.append(cmd)

    def poll(self) -> Optional[int]:
        return None if self.alive else 0

    def wait(self, timeout: Optional[float] = None) -> int:
        self.alive = False
        return 0

    terminate = kill = wait


def _arg(cmd: list[str], flag: str) -> Optional[str]:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    started: list[list[str]] = []
    monkeypatch.setattr(_FakeProc, "started", started)
    monkeypatch.setattr(process.subprocess, "Popen", _FakeProc)
    return started


def test_start_passes_the_chosen_source_and_restarts_only_on_a_change(fake_popen: list[list[str]]) -> None:
    chosen = {"source": "zoom"}
    proc = ReaderProcess("http://127.0.0.1:1", source=lambda: chosen["source"])
    assert proc.start() == "zoom" and _arg(fake_popen[0], "--source") == "zoom"
    assert proc.start() == "zoom" and len(fake_popen) == 1  # the same source: the running reader stays
    chosen["source"] = "teams"
    assert proc.start() == "teams" and len(fake_popen) == 2 and _arg(fake_popen[1], "--source") == "teams"
    chosen["source"] = "bogus"  # a hand-edited config: Zoom, as the reader itself falls back
    assert proc.start() == "zoom" and _arg(fake_popen[2], "--source") == "zoom"
    proc.start(simulate="burst:3")  # a simulation never carries a source
    assert _arg(fake_popen[3], "--source") is None and proc.mode == "simulator"
    proc.stop()
    assert not proc.running() and proc.mode is None


def test_switch_saves_and_restarts_only_a_running_reader(client, fake_popen: list[list[str]], isolated_env: Path) -> None:
    proc = client.app.state.reader_process
    # Stopped: the switch only saves; the next start reads Teams.
    res = client.put("/api/chat/reader/source", json={"source": "teams"}).json()
    assert res == {"source": "teams", "restarted": False, "running": None} and fake_popen == []
    assert load_config().reader.source == "teams"  # saved in the config file: survives an app restart
    assert client.get("/api/chat/messages").json()["reader"]["chosen"] == "teams"
    assert client.post("/api/chat/reader/start").json() == {"running": "teams", "hint": "open the meeting chat as the active tab"}
    assert _arg(fake_popen[-1], "--source") == "teams"
    # Running: switching back restarts on Zoom; choosing the running source again changes nothing.
    res = client.put("/api/chat/reader/source", json={"source": "zoom"}).json()
    assert res == {"source": "zoom", "restarted": True, "running": "zoom"} and len(fake_popen) == 2
    assert client.post("/api/chat/reader/start").json() == {"running": "zoom", "hint": "pop out the chat"}  # #256: the test-button toast says this
    assert _arg(fake_popen[-1], "--source") == "zoom"
    assert client.put("/api/chat/reader/source", json={"source": "zoom"}).json()["restarted"] is False
    assert len(fake_popen) == 2
    # A rehearsal keeps running through a switch.
    client.post("/api/chat/simulate", json={"kind": "burst", "count": 3})
    res = client.put("/api/chat/reader/source", json={"source": "teams"}).json()
    assert res == {"source": "teams", "restarted": False, "running": "simulator"}
    proc.stop()


def test_switch_is_local_only_and_knows_two_sources(client, fake_popen: list[list[str]]) -> None:
    from app.webapp.server import create_app

    assert client.put("/api/chat/reader/source", json={"source": "slack"}).status_code == 422
    with paired_remote(create_app(), "10.0.0.5") as remote:
        assert remote.put("/api/chat/reader/source", json={"source": "teams"}).json()["error"]["code"] == "local_only"
    assert load_config().reader.source == "zoom" and fake_popen == []


def test_reader_source_flag_wins_over_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(reader.sys, "platform", "win32")
    monkeypatch.setattr(reader.Reader, "run", lambda self: seen.append(self.source) or 0)
    assert reader.main(["--server", "http://127.0.0.1:1", "--source", "teams"]) == 0
    assert reader.main(["--server", "http://127.0.0.1:1"]) == 0  # no flag: the config's (zoom)
    assert seen == ["teams", "zoom"]
