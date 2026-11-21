"""Available features (``src/features.py``): every block is on by default, the choice is saved per machine in
``config.json``, an older config's ``obs.enabled`` / ``reader.enabled`` still reads as that block's choice, and a
block switched off hides its parts, refuses its API and stops its background work at once."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers.settings import FeatureKey
from src import config as config_mod
from src import features
from tests.conftest import paired_remote
from tests.fixtures.demo import build_demo_session


def _switch(client: TestClient, **choice: bool) -> dict[str, Any]:
    r = client.put("/api/settings/features", json=choice)
    assert r.status_code == 200, r.text
    return r.json()


def _block(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(f for f in payload["features"] if f["key"] == key)


# ------------------------------------------------------------------ the choice


def test_every_block_is_on_by_default_and_features_beats_a_legacy_switch() -> None:
    assert features.resolve({}) == dict.fromkeys(features.KEYS, True)
    legacy = {"obs": {"enabled": False}, "reader": {"enabled": False}}
    assert features.resolve(legacy) == {**dict.fromkeys(features.KEYS, True), "obs": False, "chat_reader": False}
    assert features.resolve({**legacy, "features": {"obs": True, "music": False}})["obs"] is True
    assert features.resolve({"features": {"music": "no", "bogus": False}}) == dict.fromkeys(features.KEYS, True)


def test_a_legacy_config_reads_as_off_without_being_rewritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    text = json.dumps({"obs": {"enabled": False, "port": 4460}, "reader": {"enabled": False}})
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FS_CONFIG_PATH", str(path))
    cfg = config_mod.load_config()
    assert features.off_keys(cfg) == ["chat_reader", "obs"] and cfg.obs.port == 4460
    assert path.read_text(encoding="utf-8") == text


def test_the_route_takes_exactly_the_blocks_the_module_defines() -> None:
    assert get_args(FeatureKey) == features.KEYS


def test_settings_lists_every_block_and_saves_a_switch_for_this_pc(client: TestClient, isolated_env: Path) -> None:
    listed = client.get("/api/settings/features").json()["features"]
    assert [f["key"] for f in listed] == list(features.KEYS)
    assert all({"title", "covers", "enabled", "available", "reason", "restart"} <= set(f) for f in listed)
    assert _block({"features": listed}, "quiz_public")["restart"] == "the tray"
    unavailable = [f for f in listed if not f["available"]]
    assert all(f["reason"] for f in unavailable)  # the test PC has no tunnel config and no certificate
    assert {"quiz_public", "remote"} <= {f["key"] for f in unavailable}
    body = _switch(client, music=False)
    assert _block(body, "music")["enabled"] is False
    assert json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))["features"]["music"] is False
    assert client.put("/api/settings/features", json={"bogus": False}).status_code == 422
    assert client.put("/api/settings/features", json={}).json()["error"]["code"] == "no_features"


def test_features_can_only_be_switched_on_this_pc(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    with paired_remote(create_app(), "10.1.2.3") as remote:
        assert remote.put("/api/settings/features", json={"music": False}).json()["error"]["code"] == "local_only"


def test_the_live_snapshot_names_the_blocks_switched_off(client: TestClient) -> None:
    with client.websocket_connect("/ws?role=presenter") as ws:
        assert ws.receive_json()["type"] == "plan"
        assert ws.receive_json()["state"]["features_off"] == ["obs"]  # the test config switches OBS off
        _switch(client, copilot=False)
        assert ws.receive_json()["state"]["features_off"] == ["obs", "copilot"]


# ---------------------------------------------------------- each block, switched off


def test_chat_reader_off_stops_the_running_reader_and_refuses_its_controls(client: TestClient, monkeypatch) -> None:
    proc = client.app.state.reader_process
    stopped: list[bool] = []
    monkeypatch.setattr(proc, "running", lambda: not stopped)
    monkeypatch.setattr(proc, "stop", lambda: stopped.append(True))
    _switch(client, chat_reader=False)
    assert stopped == [True]
    for method, path in (("post", "/api/chat/reader/start"), ("post", "/api/chat/reader/stop"), ("put", "/api/chat/reader/source")):
        r = getattr(client, method)(path, json={"source": "teams"})
        assert r.status_code == 404 and r.json()["error"]["message"] == features.OFF_SENTENCE


def test_readiness_leaves_out_the_checks_of_blocks_switched_off(client: TestClient, isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "ready", isolated_env / "sessions.local.yaml")
    keys = [c["key"] for c in client.get(f"/api/sessions/{sid}").json()["readiness"]]
    assert "reader" in keys and "obs" not in keys  # OBS is off in the test config
    _switch(client, chat_reader=False, obs=True)
    keys = [c["key"] for c in client.get(f"/api/sessions/{sid}").json()["readiness"]]
    assert "reader" not in keys and "obs" in keys


def test_obs_off_refuses_the_profile_action(client: TestClient) -> None:
    r = client.post("/api/actions/obs_profile/camera_pip")
    assert r.status_code == 404 and r.json()["error"]["code"] == "feature_off"


def test_copilot_off_stops_copilot_and_its_overlay_and_refuses_its_api(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(client.app.state.copilot, "stop", lambda: calls.append("copilot"))
    monkeypatch.setattr(client.app.state.overlay, "stop", lambda: calls.append("overlay"))
    assert client.get("/api/copilot/state").status_code == 200
    _switch(client, copilot=False)
    assert calls == ["copilot", "overlay"]
    assert client.get("/api/copilot/state").json()["error"]["code"] == "feature_off"
    assert client.post("/api/actions/copilot_toggle").json()["error"]["code"] == "feature_off"


def test_music_off_silences_it_and_refuses_its_actions_cues_and_spotify(client: TestClient, monkeypatch) -> None:
    music = client.app.state.music
    calls: list[str] = []
    monkeypatch.setattr(music, "silence", lambda: calls.append("silence"))
    _switch(client, music=False)
    assert calls == ["silence"]
    assert client.post("/api/actions/music_toggle").json()["error"]["code"] == "feature_off"
    assert client.get("/api/settings/spotify").json()["error"]["code"] == "feature_off"
    assert music.cue("question") is False


def test_quiz_public_off_stops_the_listener_and_the_link_check(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []

    async def stop() -> None:
        calls.append("stop")
        player.running = False

    player = SimpleNamespace(running=True, stop=stop)
    monkeypatch.setattr(client.app.state, "player", player)
    assert client.app.state.quiz.public_url() == ""  # the test config has no public URL at all
    _switch(client, quiz_public=False)
    assert calls == ["stop"] and client.app.state.quiz_reach.wanted() is False
    assert client.get("/api/quiz/reach").json()["error"]["code"] == "feature_off"


def test_remote_off_closes_the_page_and_refuses_a_paired_phone(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    app = create_app()
    phone = paired_remote(app)
    assert phone.get("/api/settings").status_code == 200
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        body = _switch(pc, remote=False)
        assert body["remote"]["enabled"] is False and body["remote"]["link"] is None
        assert pc.get("/remote").status_code == 404
        assert pc.post("/api/settings/remote/token").json()["error"]["code"] == "feature_off"
    r = phone.get("/api/settings")
    assert r.status_code == 401 and r.json()["error"]["code"] == "remote_off"


def test_the_tray_starts_no_tunnel_while_the_public_quiz_link_is_off(tmp_path: Path, monkeypatch) -> None:
    from app.tray import tray as tray_mod

    monkeypatch.setattr(tray_mod, "WebappManager", lambda cfg: SimpleNamespace(is_reachable=lambda: True))
    monkeypatch.setattr(tray_mod, "Tunnel", lambda **_: SimpleNamespace())
    monkeypatch.setattr(tray_mod, "WATCHDOG_LOG", tmp_path / "watchdog.log")
    on = tray_mod.TrayApp(config_mod.AppConfig(), instance=SimpleNamespace())
    off = tray_mod.TrayApp(config_mod.AppConfig(features={**features.DEFAULTS, "quiz_public": False}), instance=SimpleNamespace())
    assert on.tunnel_wanted() is True and off.tunnel_wanted() is False
