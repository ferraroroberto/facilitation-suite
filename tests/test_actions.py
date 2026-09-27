"""The Stream Deck surface: POST /api/actions/{id}[/{arg}] — home-automation's contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.demo import build_demo_session


def test_actions_drive_the_live_session(client, isolated_env: Path) -> None:
    r = client.post("/api/actions/next")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_live"
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "deck", isolated_env / "sessions.local.yaml")
    client.post("/api/live/activate", json={"session": sid})

    r = client.post("/api/actions/next", headers={"X-Automation-Source": "Stream-Deck"})
    assert r.json() == {"action_id": "next", "ok": True}
    state = client.get("/api/live").json()["state"]
    assert state["index"] == 1
    assert state["last_action"]["source"] == "stream-deck" and state["last_action"]["action"] == "next"

    assert client.post("/api/actions/goto_section/3").json()["ok"] is True
    assert client.get("/api/live").json()["state"]["item_id"] == "brk-coffee"
    assert client.post("/api/actions/blackout").status_code == 200
    assert client.get("/api/live").json()["state"]["blackout"] is True

    assert client.post("/api/actions/nope").status_code == 404
    assert client.post("/api/actions/goto_section").json()["error"]["code"] == "missing_argument"
    assert client.post("/api/actions/goto_section/x").json()["error"]["code"] == "bad_argument"


def test_the_list_carries_one_path_per_button(client) -> None:
    rows = {a["id"]: a for a in client.get("/api/actions").json()["actions"]}
    assert rows["next"]["path"] == "/api/actions/next" and rows["next"]["stream_deck"] is True
    assert rows["goto_section"]["path"] == "/api/actions/goto_section/{n}"
    assert rows["obs_profile"]["path"] == "/api/actions/obs_profile/{name}"
    assert rows["capture_toggle"]["stream_deck"] is True and rows["hide_message"]["stream_deck"] is False


def test_other_devices_need_the_remote_token(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    app = create_app()
    with TestClient(app, client=("192.168.1.20", 5000)) as remote:
        r = remote.post("/api/actions/next")
        assert r.status_code == 401 and r.json()["error"]["code"] == "remote_off"
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        pc.post("/api/settings/remote/token")
    token = app.state.config.remote.token
    with TestClient(app, client=("192.168.1.20", 5000)) as remote:
        r = remote.post("/api/actions/next", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_live"  # through the gate, to the hub
