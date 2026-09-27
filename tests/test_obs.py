"""OBS: the scene follows each item's profile over obs-websocket v5 (a faithful fake
OBS), OBS going away never stops the deck, and Settings edits profiles."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_test_config
from tests.fixtures.demo import build_demo_session
from tests.fixtures.fake_obs import FakeObs


def _until(fn, timeout: float = 6.0):  # noqa: ANN001, ANN202
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition never met")


@pytest.fixture
def obs_env(isolated_env: Path):
    fake = FakeObs(["Slides + camera", "Camera PiP", "Screen only"])
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"),
                      obs={"enabled": True, "host": "127.0.0.1", "port": fake.port, "password": "s3cret"},
                      profiles={"camera_strip": {"scene": "Slides + camera"}, "camera_pip": {"scene": "Camera PiP"},
                                "screen_only": {"scene": "Screen only"}})
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "obs", isolated_env / "sessions.local.yaml")
    from app.webapp.server import create_app

    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        yield client, fake, sid
    fake.close()


def test_the_scene_follows_the_item(obs_env) -> None:
    client, fake, sid = obs_env
    app = client.app
    _until(lambda: app.state.obs.state == "connected")
    client.post("/api/live/activate", json={"session": sid})
    _until(lambda: fake.switched[-1:] == ["Slides + camera"])  # the first slide: camera strip
    client.post("/api/live/action", json={"action": "next"})  # the map: camera PiP
    _until(lambda: fake.switched[-1:] == ["Camera PiP"])
    snap = client.get("/api/live").json()["state"]["obs"]
    assert snap["state"] == "connected" and snap["profile"] == "camera_pip"
    # by hand (Stream Deck / presenter)
    assert client.post("/api/live/action", json={"action": "obs_profile", "arg": "screen_only"}).status_code == 200
    _until(lambda: fake.switched[-1:] == ["Screen only"])
    r = client.post("/api/live/action", json={"action": "obs_profile", "arg": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_profile"
    readiness = {c["key"]: c for c in client.get(f"/api/sessions/{sid}").json()["readiness"]}
    assert readiness["obs"]["state"] == "ok" and "3 of 3 profiles" in readiness["obs"]["detail"]


def test_obs_going_away_never_stops_the_deck(obs_env) -> None:
    client, fake, sid = obs_env
    app = client.app
    _until(lambda: app.state.obs.state == "connected")
    client.post("/api/live/activate", json={"session": sid})
    fake.close()
    client.post("/api/live/action", json={"action": "next"})  # the switch fails: OBS is gone
    _until(lambda: app.state.obs.state == "disconnected")
    assert client.post("/api/live/action", json={"action": "next"}).json() == {"ok": True, "action": "next"}
    state = client.get("/api/live").json()["state"]
    assert state["index"] == 2 and state["obs"]["state"] in ("disconnected", "connecting")
    readiness = {c["key"]: c for c in client.get(f"/api/sessions/{sid}").json()["readiness"]}
    assert readiness["obs"]["state"] == "unknown"


def test_settings_edit_profiles_and_never_return_the_password(obs_env, isolated_env: Path) -> None:
    client, fake, _ = obs_env
    got = client.get("/api/settings").json()
    assert got["obs"]["password_set"] is True and "s3cret" not in json.dumps(got)
    r = client.put("/api/settings", json={"profiles": {"camera_pip": {"scene": "Camera PiP", "zone": [0.7, 0.05, 0.97, 0.32]}}})
    assert r.status_code == 200 and r.json()["profiles"]["camera_pip"]["zone"] == [0.7, 0.05, 0.97, 0.32]
    saved = json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))
    assert saved["profiles"]["camera_pip"]["zone"] == [0.7, 0.05, 0.97, 0.32]
    assert saved["obs"]["password"] == "s3cret"  # untouched keys survive
    assert client.put("/api/settings", json={"profiles": {"camera_pip": {"zone": [0.9, 0, 0.1, 1]}}}).status_code == 422
    assert client.put("/api/settings", json={"profiles": {"wide": {"scene": "x"}}}).status_code == 404
    r = client.put("/api/settings", json={"obs": {"enabled": False}})
    assert r.json()["obs"]["enabled"] is False
    _until(lambda: client.app.state.obs.state == "off")


def test_settings_cannot_be_changed_remotely(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    with TestClient(create_app(), client=("10.1.2.3", 5000)) as remote:
        assert remote.get("/api/settings").status_code == 200
        assert remote.put("/api/settings", json={"obs": {"enabled": True}}).json()["error"]["code"] == "local_only"
