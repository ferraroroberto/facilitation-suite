"""The copilot's consent gate and the retention of its recordings. Retention runs on synthetic files in a
temp dir only: no real session folder or recording is ever read or removed."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.config import CopilotConfig, load_config
from src.copilot.retention import DAY_S, purge_recordings
from src.copilot.service import CopilotError, CopilotService
from src.jsonl import read_jsonl
from src.live.actions import ACTIONS, Action, run_action
from src.live.hub import LiveHub
from src.sessions import readiness
from src.sessions.model import COPILOT_CONSENT, Session
from src.sessions.offline import OfflineReport
from src.sessions.store import SessionStore
from tests.conftest import SAMPLE_CONFIG, write_test_config
from tests.fixtures.demo import build_demo_session

OFF = CopilotConfig(capture="off", hub_url="")


@pytest.fixture
def live(isolated_env: Path) -> LiveHub:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "live", isolated_env / "sessions.local.yaml")
    hub = LiveHub(SessionStore(load_config()))
    hub.activate(sid)
    return hub


def session_yaml(hub: LiveHub) -> dict:
    return yaml.safe_load((hub.folder / "session.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ consent gate


def test_unconfirmed_copilot_refuses_to_record_and_says_why(live: LiveHub, monkeypatch: pytest.MonkeyPatch) -> None:
    copilot = CopilotService(live, lambda: OFF)
    assert copilot.snapshot()["consent"] is False
    with pytest.raises(CopilotError) as err:
        copilot.start()
    assert err.value.code == "consent_required" and err.value.status == 409 and not copilot.on
    # the Stream Deck / remote path is the same live action and is refused the same way
    monkeypatch.setitem(ACTIONS, "copilot_toggle", Action("copilot_toggle", "Copilot on/off", lambda hub, arg: copilot.toggle()))
    with pytest.raises(CopilotError):
        run_action(live, "copilot_toggle")
    assert not copilot.on


def test_once_confirmed_it_does_not_ask_again_for_that_session(live: LiveHub) -> None:
    copilot = CopilotService(live, lambda: OFF)
    copilot.confirm_consent()
    for _ in range(2):  # on, off, on, off: never asked again
        copilot.toggle()
        assert copilot.on and copilot.snapshot()["consent"] is True
        copilot.toggle()
        assert not copilot.on


def test_the_confirmation_lands_in_session_yaml_and_events_once(live: LiveHub) -> None:
    copilot = CopilotService(live, lambda: OFF)
    copilot.confirm_consent()
    copilot.confirm_consent()  # asked once: a second confirmation is not a second record
    assert session_yaml(live)["checklist"][COPILOT_CONSENT] is True
    events = [e for e in read_jsonl(live.folder / "live" / "events.jsonl") if e["event"] == "copilot_consent"]
    assert len(events) == 1 and events[0]["at"]


def test_consent_belongs_to_the_session_and_survives_going_live_again(live: LiveHub) -> None:
    sid = live.session_id
    CopilotService(live, lambda: OFF).confirm_consent()
    live.deactivate()
    live.activate(sid)
    assert CopilotService(live, lambda: OFF).consented()


def test_consent_not_saved_changes_nothing(live: LiveHub, monkeypatch: pytest.MonkeyPatch) -> None:
    copilot = CopilotService(live, lambda: OFF)

    def fail(sid: str, change: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(live.store, "edit", fail)
    with pytest.raises(CopilotError) as err:
        copilot.confirm_consent()
    assert err.value.code == "consent_not_saved" and not copilot.consented()
    assert not [e for e in read_jsonl(live.folder / "live" / "events.jsonl") if e["event"] == "copilot_consent"]


def test_a_plan_saved_from_an_older_copy_keeps_the_recorded_consent(live: LiveHub) -> None:
    older = live.store.load(live.session_id)  # the Sessions tab loaded it before the presenter confirmed
    CopilotService(live, lambda: OFF).confirm_consent()
    live.store.save_plan(live.session_id, older)
    assert live.store.load(live.session_id).checklist[COPILOT_CONSENT] is True


def test_plan_saved_while_live_refreshes_the_hubs_view(live: LiveHub) -> None:
    sid = live.session_id
    marked = live.store.load(sid)
    marked.checklist[COPILOT_CONSENT] = True  # marked in the file by another route
    live.store.save_plan(sid, marked)
    live._reload(sid)
    assert CopilotService(live, lambda: OFF).consented()


def test_readiness_shows_the_consent_row(tmp_path: Path) -> None:
    offline = OfflineReport(state="ok", total=0, local=0)
    rows = {c["key"]: c for c in readiness.build(tmp_path, Session(), offline)}
    assert rows[COPILOT_CONSENT]["state"] == "todo" and "consent recorded" in rows[COPILOT_CONSENT]["detail"]
    rows = {c["key"]: c for c in readiness.build(tmp_path, Session(checklist={COPILOT_CONSENT: True}), offline)}
    assert rows[COPILOT_CONSENT]["state"] == "ok"


# ------------------------------------------------------------------- retention


def recording(folder: Path, name: str, age_days: float, now: float) -> Path:
    path = folder / name
    path.write_bytes(b"RIFF synthetic")
    stamp = now - age_days * DAY_S
    os.utime(path, (stamp, stamp))
    return path


def test_retention_removes_old_recordings_only_and_logs_each(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    now = time.time()
    copilot = tmp_path / "live" / "copilot"
    copilot.mkdir(parents=True)
    old = recording(copilot, "me-100000.wav", 40, now)
    edge = recording(copilot, "them-100000.wav", 29, now)
    fresh = recording(copilot, "me-110000.wav", 0.1, now)
    transcript = recording(copilot, "transcript.jsonl", 400, now)  # never a recording
    hints = recording(copilot, "hints.jsonl", 400, now)
    nested = copilot / "old"
    nested.mkdir()
    deep = recording(nested, "deep.wav", 400, now)  # only the folder's own files
    with caplog.at_level(logging.INFO, logger="src.copilot.retention"):
        removed = purge_recordings(tmp_path, 30, now)
    assert removed == ["me-100000.wav"] and not old.exists()
    assert all(p.exists() for p in (edge, fresh, transcript, hints, deep))
    assert "removed recording me-100000.wav" in caplog.text and "30-day" in caplog.text
    assert purge_recordings(tmp_path, 30, now) == []  # nothing left to remove on the next open


def test_the_default_keeps_everything(tmp_path: Path) -> None:
    now = time.time()
    copilot = tmp_path / "live" / "copilot"
    copilot.mkdir(parents=True)
    ancient = recording(copilot, "me-090000.wav", 3000, now)
    assert CopilotConfig().recording_retention_days == 0
    assert purge_recordings(tmp_path, CopilotConfig().recording_retention_days, now) == []
    assert purge_recordings(tmp_path, -5, now) == []  # a hand-edited nonsense value keeps too
    assert ancient.exists()


def test_a_session_without_recordings_is_left_alone(tmp_path: Path) -> None:
    assert purge_recordings(tmp_path, 7) == []
    assert not (tmp_path / "live").exists()


def test_a_recording_that_cannot_be_removed_is_logged_and_the_rest_goes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    now = time.time()
    copilot = tmp_path / "live" / "copilot"
    copilot.mkdir(parents=True)
    stuck = recording(copilot, "a-stuck.wav", 90, now)
    gone = recording(copilot, "b-gone.wav", 90, now)
    real_unlink = Path.unlink

    def unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "a-stuck.wav":
            raise PermissionError("held by another process")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level(logging.WARNING, logger="src.copilot.retention"):
        assert purge_recordings(tmp_path, 7, now) == ["b-gone.wav"]
    assert stuck.exists() and not gone.exists() and "a-stuck.wav could not be removed" in caplog.text


def test_going_live_applies_the_retention_setting(live: LiveHub) -> None:
    sid, now = live.session_id, time.time()
    folder = live.folder / "live" / "copilot"
    folder.mkdir(parents=True)
    old = recording(folder, "me-100000.wav", 10, now)
    young = recording(folder, "me-120000.wav", 1, now)
    CopilotService(live, lambda: CopilotConfig(capture="off", hub_url="", recording_retention_days=7))
    live.deactivate()
    live.activate(sid)
    assert not old.exists() and young.exists()


# --------------------------------------------------------------------- the API


@pytest.fixture
def app(isolated_env: Path):
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(hub_url="", capture="off", recording_retention_days=7)
    copilot["overlay"]["enabled"] = False
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), copilot=copilot)
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "live", isolated_env / "sessions.local.yaml")
    from app.webapp.server import create_app

    application = create_app()
    application.state.demo_sid = sid
    return application


@pytest.fixture
def pc(app) -> Iterator[TestClient]:
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_api_the_toggle_is_refused_until_the_presenter_confirms(pc: TestClient, app) -> None:
    assert pc.post("/api/copilot/consent").json()["error"]["code"] == "not_live"
    pc.post("/api/live/activate", json={"session": app.state.demo_sid})
    state = pc.get("/api/live").json()["state"]["copilot"]
    assert state["consent"] is False
    refused = pc.post("/api/actions/copilot_toggle")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "consent_required"
    assert pc.post("/api/copilot/consent").json()["consent"] is True
    assert pc.post("/api/actions/copilot_toggle").status_code == 200
    assert pc.get("/api/live").json()["state"]["copilot"]["on"] is True


def test_api_opening_a_session_removes_recordings_past_the_retention(pc: TestClient, app, isolated_env: Path) -> None:
    now = time.time()
    folder = isolated_env / "sessions" / "demo" / "live" / "live" / "copilot"
    folder.mkdir(parents=True)
    old = recording(folder, "me-100000.wav", 30, now)
    young = recording(folder, "me-120000.wav", 2, now)
    assert pc.get(f"/api/sessions/{app.state.demo_sid}").status_code == 200
    assert not old.exists() and young.exists()


def test_api_the_retention_setting_is_checked_and_kept(pc: TestClient, isolated_env: Path) -> None:
    assert pc.get("/api/copilot/settings").json()["recording_retention_days"] == 7
    r = pc.put("/api/copilot/settings", json={"recording_retention_days": 30})
    assert r.status_code == 200 and r.json()["recording_retention_days"] == 30
    saved = json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))["copilot"]
    assert saved["recording_retention_days"] == 30 and saved["hub_url"] == ""  # merged, not replaced
    assert pc.put("/api/copilot/settings", json={"recording_retention_days": 0}).json()["recording_retention_days"] == 0
    assert pc.put("/api/copilot/settings", json={"recording_retention_days": -1}).status_code == 422
