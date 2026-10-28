"""The local listener: the main app again in plain HTTP on ``127.0.0.1:<local_port>``, started
by the main app's lifespan — loopback only, and optional, so a busy port never takes the main app
down."""

from __future__ import annotations

import json
import os
import socket
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from websockets.sync.client import connect

from tests.conftest import write_test_config

LISTENER_LOG = "app.webapp.local_listener"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def _with_local_port(root: Path, port: int) -> None:
    write_test_config(Path(os.environ["FS_CONFIG_PATH"]), session_root=str(root / "sessions"), local_port=port)


@pytest.fixture
def local(isolated_env: Path) -> Iterator[tuple[TestClient, int]]:
    """The main app (in-process) with its local listener on a real free port."""
    from app.webapp.server import create_app

    port = _free_port()
    _with_local_port(isolated_env, port)
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as main:
        yield main, port


def test_listener_serves_the_main_app(local) -> None:
    main, port = local
    assert main.app.state.local_listener.running
    base = f"http://127.0.0.1:{port}"
    assert json.loads(_get(base + "/healthz")[1]) == {"status": "ok"}
    assert json.loads(_get(base + "/api/version")[1])["git_sha"]
    for path in ("/", "/presenter", "/stage"):
        assert _get(base + path)[0] == 200, path
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] in ("plan", "state")


def test_listener_binds_loopback_only(local) -> None:
    main, port = local
    sock = main.app.state.local_listener.server.servers[0].sockets[0]
    assert sock.getsockname()[0] == "127.0.0.1"
    # The machine's own LAN address is another interface: nothing listens there.
    lan = socket.gethostbyname(socket.gethostname())
    if lan.startswith("127."):
        pytest.skip("this host has no non-loopback IPv4 address")
    with pytest.raises(OSError), socket.create_connection((lan, port), timeout=2):
        pass


def test_listener_stops_with_the_main_app(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    port = _free_port()
    _with_local_port(isolated_env, port)
    with TestClient(create_app(), client=("127.0.0.1", 50000)):
        assert _get(f"http://127.0.0.1:{port}/healthz")[0] == 200
    with socket.socket() as s:  # the port is free again once the main app stopped
        s.bind(("127.0.0.1", port))


@pytest.mark.parametrize("holder", ["127.0.0.1", "0.0.0.0"])
def test_busy_port_is_logged_and_the_main_app_keeps_serving(isolated_env: Path, caplog, holder: str) -> None:
    from app.webapp.server import create_app

    with socket.socket() as squatter:
        squatter.bind((holder, 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        _with_local_port(isolated_env, port)
        with caplog.at_level("ERROR", logger=LISTENER_LOG), TestClient(create_app(), client=("127.0.0.1", 50000)) as main:
            assert not main.app.state.local_listener.running
            assert main.get("/healthz").json() == {"status": "ok"}
    errors = [r.getMessage() for r in caplog.records if r.name == LISTENER_LOG and r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "❌ local listener" in errors[0] and f":{port} is busy" in errors[0]


def test_port_zero_turns_the_listener_off(client) -> None:
    assert client.app.state.local_listener.running is False
    assert client.app.state.config.local_port == 0


def test_local_port_defaults_and_sample() -> None:
    from src.config import CONFIG_SAMPLE_PATH, AppConfig

    assert AppConfig().local_port == 8456
    assert json.loads(CONFIG_SAMPLE_PATH.read_text(encoding="utf-8"))["local_port"] == 8456
