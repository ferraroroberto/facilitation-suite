"""The Analyze session button's server side: the command it copies and the inputs it reports."""

from __future__ import annotations

from pathlib import Path

from src.results.analysis import command
from tests.fixtures.demo import build_demo_session, write_demo_run


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
