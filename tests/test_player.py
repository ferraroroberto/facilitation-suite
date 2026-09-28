"""The public quiz player listener (#49): a separate app on loopback, started by
the main app's lifespan, serving ``/play*`` only — and optional, so a busy port
never takes the main app down."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from app.player.app import create_player_app
from tests.conftest import write_test_config

LISTENER_LOG = "app.player.listener"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _with_player_port(root: Path, port: int) -> None:
    write_test_config(Path(os.environ["FS_CONFIG_PATH"]), session_root=str(root / "sessions"),
                      quiz={"public_port": port, "public_url": ""})


@pytest.fixture
def player(isolated_env: Path) -> Iterator[tuple[TestClient, str]]:
    """The main app (in-process) with its player listener on a real free port."""
    from app.webapp.server import create_app

    port = _free_port()
    _with_player_port(isolated_env, port)
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as main:
        yield main, f"127.0.0.1:{port}"


def test_player_app_routes_are_play_only() -> None:
    app = create_player_app()
    paths = [r.path for r in app.routes]
    assert paths, "the player app has no routes"
    assert all(p == "/play" or p.startswith("/play/") for p in paths), paths
    assert not any(isinstance(r, Mount) for r in app.routes)  # no static mounts shared with :8449
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_listener_serves_the_player_app_only(player) -> None:
    main, addr = player
    assert main.app.state.player.running
    status, html = _get(f"http://{addr}/play")
    assert status == 200 and "<title>Quiz</title>" in html
    status, body = _get(f"http://{addr}/play/api/ping")
    assert status == 200 and json.loads(body)["ok"] is True
    with connect(f"ws://{addr}/play/ws", open_timeout=5) as ws:
        ws.send("hello")
        assert ws.recv(timeout=5) == "hello"

    # Nothing of the main app is reachable through the player port…
    for path in ("/presenter", "/stage", "/", "/remote", "/api/sessions", "/api/version", "/healthz",
                 "/static/css/tokens.css", "/docs", "/openapi.json"):
        assert _get(f"http://{addr}{path}")[0] == 404, path
    with pytest.raises(InvalidStatus):
        connect(f"ws://{addr}/ws", open_timeout=5)
    # …and the main app itself is untouched.
    assert main.get("/healthz").json() == {"status": "ok"}
    assert main.get("/play").status_code == 404


def test_listener_stops_with_the_main_app(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    port = _free_port()
    _with_player_port(isolated_env, port)
    with TestClient(create_app(), client=("127.0.0.1", 50000)):
        assert _get(f"http://127.0.0.1:{port}/play")[0] == 200
    with socket.socket() as s:  # the port is free again once the main app stopped
        s.bind(("127.0.0.1", port))


@pytest.mark.parametrize("holder", ["127.0.0.1", "0.0.0.0"])
def test_busy_port_is_logged_and_the_main_app_keeps_serving(isolated_env: Path, caplog, holder: str) -> None:
    from app.webapp.server import create_app

    with socket.socket() as squatter:
        squatter.bind((holder, 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        _with_player_port(isolated_env, port)
        with caplog.at_level("ERROR", logger=LISTENER_LOG), TestClient(create_app(), client=("127.0.0.1", 50000)) as main:
            assert not main.app.state.player.running
            assert main.get("/healthz").json() == {"status": "ok"}
    errors = [r.getMessage() for r in caplog.records if r.name == LISTENER_LOG and r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "❌ quiz player listener" in errors[0] and f":{port} is busy" in errors[0]


def test_port_zero_turns_the_listener_off(client, caplog) -> None:
    assert client.app.state.player.running is False
    assert client.app.state.config.quiz.public_port == 0


def test_quiz_config_defaults_and_sample() -> None:
    from src.config import CONFIG_SAMPLE_PATH, AppConfig

    assert AppConfig().quiz.public_port == 8450
    assert AppConfig().quiz.public_url == ""
    sample = json.loads(CONFIG_SAMPLE_PATH.read_text(encoding="utf-8"))
    assert sample["quiz"] == {"public_port": 8450, "public_url": ""}
