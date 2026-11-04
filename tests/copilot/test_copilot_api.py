"""The copilot through the real app: the toggle action, an injected transcript → hints on the live
snapshot (presenter and overlay roles), the overlay settings and the session's local-only switch."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import SAMPLE_CONFIG, paired_remote, write_test_config
from tests.fixtures.demo import build_demo_session
from tests.fixtures.fake_hub import FakeHub


@pytest.fixture
def fake() -> Iterator[FakeHub]:
    hub = FakeHub()
    yield hub
    hub.close()


@pytest.fixture
def app(isolated_env: Path, fake: FakeHub):
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(hub_url=fake.url, capture="off", hint_interval_s=0)
    copilot["overlay"]["enabled"] = False  # no window from a test
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), copilot=copilot)
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "live", isolated_env / "sessions.local.yaml")
    from app.webapp.server import create_app

    application = create_app()
    application.state.demo_sid = sid
    return application


def go_live_with_consent(pc: TestClient, app) -> None:
    assert pc.post("/api/live/activate", json={"session": app.state.demo_sid}).status_code == 200
    assert pc.post("/api/copilot/consent").status_code == 200


def live_copilot(pc: TestClient) -> dict:
    return pc.get("/api/live").json()["state"]["copilot"]


def test_injected_transcript_reaches_presenter_and_overlay(app, fake: FakeHub) -> None:
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        go_live_with_consent(pc, app)
        r = pc.post("/api/copilot/transcript", json={"lines": [{"speaker": "them", "text": "Hi"}]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "copilot_off"
        assert pc.post("/api/actions/copilot_toggle").status_code == 200
        assert live_copilot(pc)["on"] and live_copilot(pc)["channels"] == []  # capture off: no device opened
        with pc.websocket_connect("/ws?role=overlay") as overlay:
            assert overlay.receive_json()["type"] == "plan"
            r = pc.post("/api/copilot/transcript", json={"lines": [{"speaker": "me", "text": "What would you try first?"},
                                                                  {"speaker": "them", "text": "The cheap prototype"}]})
            assert r.json() == {"added": 2}
            seen = None
            for _ in range(40):
                msg = overlay.receive_json()
                if msg["type"] == "state" and msg["state"]["copilot"]["hints"]:
                    seen = msg["state"]["copilot"]
                    break
            assert seen is not None and seen["hints"][0]["text"] == "Ask which idea they would test first"
            assert seen["hints_item_id"] == pc.get("/api/live").json()["state"]["item_id"]
        prompt = fake.calls_to("/v1/chat/completions")[0]["messages"][1]["content"]
        assert "me: What would you try first?" in prompt and "them: The cheap prototype" in prompt
        assert pc.post("/api/actions/copilot_toggle").status_code == 200
        assert not live_copilot(pc)["on"] and live_copilot(pc)["hints"] == []


def test_overlay_settings_are_checked_and_kept(app, isolated_env: Path) -> None:
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        body = pc.get("/api/copilot/settings").json()
        assert body["overlay"]["position"] == "top-center" and "Segoe UI" in body["fonts"] and isinstance(body["screens"], list)
        r = pc.put("/api/copilot/settings", json={"overlay": {"position": "bottom-right", "width": 900, "mode": "cue",
                                                             "background": "#202020", "font": "Not A Font"}})
        assert r.status_code == 200 and r.json()["overlay"]["position"] == "bottom-right" and r.json()["overlay"]["font"] == "Segoe UI"
        saved = json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))["copilot"]
        assert saved["overlay"]["width"] == 900 and saved["overlay"]["mode"] == "cue" and saved["hub_url"]  # merged, not replaced
        assert pc.put("/api/copilot/settings", json={"overlay": {"text_color": "white"}}).status_code == 422
        assert pc.put("/api/copilot/settings", json={"overlay": {"position": "under-the-desk"}}).status_code == 422
        assert live_copilot(pc)["overlay"]["mode"] == "cue"  # an open overlay follows from the snapshot
        assert pc.post("/api/copilot/sample").status_code == 200 and live_copilot(pc)["sample_at"]


def test_mode_is_the_live_sessions_and_checked(app, isolated_env: Path) -> None:
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        r = pc.put("/api/copilot/mode", json={"mode": "interview", "interviewee": "Alex Doe"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_live"
        go_live_with_consent(pc, app)
        r = pc.put("/api/copilot/mode", json={"mode": "panel"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
        body = pc.put("/api/copilot/mode", json={"mode": "interview", "interviewee": "Alex Doe"}).json()
        assert (body["mode"], body["interviewee"]) == ("interview", "Alex Doe")
        yaml_text = (isolated_env / "sessions" / "demo" / "live" / "session.yaml").read_text(encoding="utf-8")
        assert "copilot_mode: interview" in yaml_text and "copilot_interviewee: Alex Doe" in yaml_text
        assert live_copilot(pc)["interviewee"] == "Alex Doe"
        conversation = pc.get(f"/api/sessions/{app.state.demo_sid}/results/conversation").json()
        assert (conversation["mode"], conversation["interviewee"]) == ("interview", "Alex Doe")  # the Results card names them


def test_local_only_is_the_sessions_and_phones_cannot_inject(app, isolated_env: Path) -> None:
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        go_live_with_consent(pc, app)
        assert pc.put("/api/copilot/local-only", json={"local_only": True}).json()["local_only"] is True
        yaml_text = (isolated_env / "sessions" / "demo" / "live" / "session.yaml").read_text(encoding="utf-8")
        assert "copilot_local_only: true" in yaml_text
        pc.post("/api/actions/copilot_toggle")
    with paired_remote(app) as phone:
        r = phone.post("/api/copilot/transcript", json={"lines": [{"speaker": "them", "text": "x"}]})
        assert r.status_code == 403 and r.json()["error"]["code"] == "local_only"
        assert phone.put("/api/copilot/settings", json={"overlay": {"width": 500}}).status_code == 403
