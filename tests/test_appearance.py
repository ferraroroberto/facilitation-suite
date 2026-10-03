"""One light/dark setting for the app, the presenter and the phone remote (#92)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import config as config_mod
from tests.conftest import paired_remote


def _state(client: TestClient) -> dict:
    return client.get("/api/live").json()["state"]


def test_defaults_to_system(client: TestClient) -> None:
    assert client.get("/api/settings/appearance").json() == {"appearance": "system"}
    assert client.get("/api/settings").json()["appearance"] == "system"
    assert _state(client)["appearance"] == "system"


def test_set_is_saved_and_pushed_to_every_open_page(client: TestClient, isolated_env: Path) -> None:
    with client.websocket_connect("/ws?role=presenter") as presenter, client.websocket_connect("/ws?role=app") as app_tab:
        for ws in (presenter, app_tab):
            assert ws.receive_json()["type"] == "plan"
            assert ws.receive_json()["state"]["appearance"] == "system"
        r = client.put("/api/settings/appearance", json={"appearance": "dark"})
        assert r.status_code == 200 and r.json() == {"appearance": "dark"}
        for ws in (presenter, app_tab):
            msg = ws.receive_json()
            assert msg["type"] == "state" and msg["state"]["appearance"] == "dark"
    saved = json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))
    assert saved["appearance"] == "dark"
    assert saved["obs"]["enabled"] is False  # the rest of the file is kept
    assert client.get("/api/settings/appearance").json() == {"appearance": "dark"}
    assert _state(client)["appearance"] == "dark"


def test_only_the_three_choices(client: TestClient) -> None:
    for bad in ("blue", "", None, "DARK"):
        assert client.put("/api/settings/appearance", json={"appearance": bad}).status_code == 422
    assert client.get("/api/settings/appearance").json() == {"appearance": "system"}


def test_the_paired_phone_sets_it_too_but_not_a_stranger(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    app = create_app()
    with paired_remote(app) as phone:
        assert phone.put("/api/settings/appearance", json={"appearance": "light"}).json() == {"appearance": "light"}
    with TestClient(app, client=("100.64.0.10", 5000)) as stranger:
        r = stranger.put("/api/settings/appearance", json={"appearance": "dark"})
        assert r.status_code == 401
    assert app.state.config.appearance == "light"


def test_an_unknown_value_in_the_file_falls_back_to_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"appearance": "sepia"}), encoding="utf-8")
    monkeypatch.setenv("FS_CONFIG_PATH", str(path))
    assert config_mod.load_config().appearance == "system"
    path.write_text(json.dumps({"appearance": "light"}), encoding="utf-8")
    assert config_mod.load_config().appearance == "light"


def test_a_click_on_the_stage_goes_on_only_when_switched_on(client: TestClient, isolated_env: Path) -> None:
    """#191: off by default; the setting is saved and the open stage hears it at once."""
    assert client.get("/api/settings").json()["stage_click"] == {"advance": False}
    assert _state(client)["stage_click"] == {"advance": False}
    with client.websocket_connect("/ws?role=stage") as stage:
        assert stage.receive_json()["type"] == "plan"
        assert stage.receive_json()["state"]["stage_click"] == {"advance": False}
        assert client.put("/api/settings", json={"stage_click": {"advance": True}}).json()["stage_click"] == {"advance": True}
        while True:  # the pushed snapshot (a stage's own hello may be queued before it)
            msg = stage.receive_json()
            if msg["type"] == "state" and msg["state"]["stage_click"]["advance"]:
                break
    assert json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))["stage_click"] == {"advance": True}
    assert client.put("/api/settings", json={"stage_click": {"advance": False}}).json()["stage_click"] == {"advance": False}
