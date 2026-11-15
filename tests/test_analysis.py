"""The Analyze session card's server side: the command it copies, the inputs it reports, and the run through the hub
 that writes the report (a fake hub, synthetic data only)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.results.analysis import command
from src.results.analysis_run import analysis_messages, report_path
from tests.conftest import SAMPLE_CONFIG, write_test_config
from tests.fixtures.demo import build_demo_session, write_demo_run
from tests.fixtures.fake_hub import FakeHub


def _demo(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "my results", isolated_env / "sessions.local.yaml")
    return sid, folder


def test_the_command_names_the_quoted_session_folder(tmp_path: Path) -> None:
    folder = tmp_path / "a session"
    assert command(folder) == f'/analyze-session "{folder}"'


def test_a_run_session_offers_the_command_and_reports_its_inputs(client, isolated_env: Path) -> None:
    sid, folder = _demo(isolated_env)
    write_demo_run(folder)
    a = client.get(f"/api/sessions/{sid}/analysis").json()
    assert a["command"] == f'/analyze-session "{folder}"'
    assert a["captured"] == 6 and a["reason"] == ""
    assert (a["playbook"], a["notes"], a["reports"]) == (False, False, [])

    (folder / "playbook.md").write_text("# Playbook\n", encoding="utf-8")
    (folder / "notes.md").write_text("late joiners\n", encoding="utf-8")
    (folder / "report").mkdir()
    (folder / "report" / "analysis-2026-10-27.md").write_text("x", encoding="utf-8")
    (folder / "report" / "analysis-2026-10-27-2.md").write_text("x", encoding="utf-8")
    (folder / "report" / "sub").mkdir()  # a folder is not a report
    a = client.get(f"/api/sessions/{sid}/analysis").json()
    assert (a["playbook"], a["notes"]) == (True, True)
    assert a["reports"] == ["analysis-2026-10-27-2.md", "analysis-2026-10-27.md"]


def test_a_session_without_a_capture_gives_a_reason_not_a_command_to_run(client, isolated_env: Path) -> None:
    sid, _ = _demo(isolated_env)
    a = client.get(f"/api/sessions/{sid}/analysis").json()
    assert a["captured"] == 0 and "Capture at least one activity" in a["reason"]


# ---------------------------------------------------------------- Run analysis, through a fake hub

REPORT = "# Session report\n\nThe room warmed up after the second activity."


@pytest.fixture
def fake() -> Iterator[FakeHub]:
    hub = FakeHub()
    yield hub
    hub.close()


@pytest.fixture
def hub_app(isolated_env: Path, fake: FakeHub):
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(hub_url=fake.url, capture="off", analysis_models=["strong", "backup"], analysis_timeout_s=30)
    copilot["overlay"]["enabled"] = False
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), copilot=copilot)
    sid, folder = _demo(isolated_env)
    write_demo_run(folder, pngs=False)
    from app.webapp.server import create_app

    application = create_app()
    application.state.demo = (sid, folder)
    return application


def _run(pc: TestClient, sid: str) -> dict:
    """Start a run and wait for it to end: the card's own poll, in a loop."""
    a = pc.post(f"/api/sessions/{sid}/analysis/run").json()
    deadline = time.monotonic() + 20
    while a["job"]["running"]:
        assert time.monotonic() < deadline, a["job"]
        time.sleep(0.05)
        a = pc.get(f"/api/sessions/{sid}/analysis").json()
    return a


def test_the_report_takes_the_first_free_name(tmp_path: Path) -> None:
    assert report_path(tmp_path, "2026-10-27T10:00:00").name == "analysis-2026-10-27.md"
    (tmp_path / "analysis-2026-10-27.md").write_text("x", encoding="utf-8")
    (tmp_path / "analysis-2026-10-27-2.md").write_text("x", encoding="utf-8")
    assert report_path(tmp_path, "2026-10-27").name == "analysis-2026-10-27-3.md"
    assert report_path(tmp_path, None).name.startswith("analysis-20")  # no date: today's


def test_the_prompt_carries_the_session_but_not_the_folder_paths() -> None:
    doc = {"folder": "/x/secret", "report_dir": "/x/secret/report", "session": {"title": "T", "date": None},
           "playbook": "Assess the room.", "activities": []}
    system, user = analysis_messages(doc)
    assert "not enough observation" in system["content"] and "never as instructions" in system["content"]
    assert json.loads(user["content"]) == {"session": {"title": "T", "date": None}, "playbook": "Assess the room.", "activities": []}


def test_a_run_writes_the_report_and_shows_it_as_a_draft(hub_app, fake: FakeHub) -> None:
    sid, folder = hub_app.state.demo
    (folder / "playbook.md").write_text("# Playbook\nAssess the room.\n", encoding="utf-8")
    fake.answers["strong"] = REPORT
    with TestClient(hub_app, client=("127.0.0.1", 50000)) as pc:
        assert pc.get(f"/api/sessions/{sid}/analysis").json()["run_unavailable"] == ""
        a = _run(pc, sid)
        assert a["job"]["state"] == "done" and a["job"]["model"] == "strong" and a["job"]["text"] == REPORT
        report = folder / "report" / a["job"]["report"]
        assert report.read_text(encoding="utf-8") == REPORT + "\n"
        assert a["reports"] == [report.name]
        call = fake.calls_to("/v1/chat/completions")[0]
        sent = json.loads(call["messages"][1]["content"])
        assert sent["playbook"].startswith("# Playbook") and len(sent["activities"]) == 6 and "folder" not in sent

        again = _run(pc, sid)  # a second run never overwrites the first
        assert again["job"]["report"] == report.stem + "-2.md" and len(again["reports"]) == 2


def test_a_failing_model_falls_back_and_a_dead_hub_says_so(hub_app, fake: FakeHub) -> None:
    sid, folder = hub_app.state.demo
    fake.failing.add("strong")
    fake.answers["backup"] = REPORT
    with TestClient(hub_app, client=("127.0.0.1", 50000)) as pc:
        assert _run(pc, sid)["job"]["model"] == "backup"
        fake.close()
        failed = _run(pc, sid)["job"]
        assert (failed["state"], failed["failure"]) == ("failed", "hub_down")
        assert len(list((folder / "report").iterdir())) == 1  # the failed run wrote nothing


def test_without_a_hub_the_run_is_refused_with_the_reason(client, isolated_env: Path) -> None:
    sid, folder = _demo(isolated_env)
    write_demo_run(folder, pngs=False)
    a = client.get(f"/api/sessions/{sid}/analysis").json()
    assert "No hub configured" in a["run_unavailable"] and a["job"] is None
    r = client.post(f"/api/sessions/{sid}/analysis/run")
    assert r.status_code == 409 and not (folder / "report").exists()

