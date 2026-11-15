"""Results → Finish session (``app/webapp/routers/wrapup.py``): rebuild, the PDFs, the Excel report and the analysis in one call."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.webapp.routers import results
from src.activities import rebuild
from tests.fixtures.demo import build_demo_session, write_demo_run


@pytest.fixture
def ran(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """A finished demo session; the PDF printer is stubbed (the real one is covered in test_results)."""
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "wrapup", isolated_env / "sessions.local.yaml")
    write_demo_run(folder)

    def fake_print(_html: str, out: Path) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"%PDF-1.7 stub")
        return 3

    monkeypatch.setattr(results, "_print", fake_print)
    return sid, folder


def _steps(body: dict) -> dict[str, tuple[str, str]]:
    return {s["key"]: (s["status"], s["message"]) for s in body["steps"]}


def test_finish_runs_every_step_in_order_and_skips_a_rebuild_with_nothing_to_change(client, ran: tuple[str, Path],
                                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    sid, folder = ran
    started: list[str] = []
    monkeypatch.setattr(client.app.state.analysis, "start", lambda s, *_: started.append(s))
    r = client.post(f"/api/sessions/{sid}/wrap-up")
    assert r.status_code == 200, r.text
    assert [s["key"] for s in r.json()["steps"]] == ["rebuild", "pdf", "xlsx", "analysis"]
    steps = _steps(r.json())
    assert steps["rebuild"] == ("done", "Nothing to change")
    assert steps["pdf"] == ("done", "3 pages") and steps["xlsx"] == ("done", "Saved")
    assert steps["analysis"][0] == "started" and started == [sid]
    assert rebuild.backups(folder) == []  # no empty backup left behind
    exports = client.get(f"/api/sessions/{sid}/results").json()["exports"]
    assert exports["pdf"]["file"] == "session-full.pdf" and exports["xlsx"]["file"] == "report.xlsx"


def test_a_live_session_skips_the_rebuild_and_no_hub_skips_the_analysis(client, ran: tuple[str, Path]) -> None:
    sid, _ = ran
    client.app.state.live.session_id = sid
    steps = _steps(client.post(f"/api/sessions/{sid}/wrap-up").json())
    client.app.state.live.session_id = None
    assert steps["rebuild"][0] == "skipped" and "live" in steps["rebuild"][1]
    assert steps["pdf"][0] == steps["xlsx"][0] == "done"
    assert steps["analysis"][0] == "skipped" and "hub" in steps["analysis"][1].lower()


def test_a_failed_rebuild_stops_the_exports_so_they_never_carry_stale_results(client, ran: tuple[str, Path],
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    sid, folder = ran
    monkeypatch.setattr(rebuild, "preview", lambda _f: {"activities": [{}], "changed": 1})

    def broken(_f: Path) -> dict:
        raise rebuild.RebuildError(502, "render_failed", "1 stage picture(s) could not be drawn")

    monkeypatch.setattr(rebuild, "apply", broken)
    steps = _steps(client.post(f"/api/sessions/{sid}/wrap-up").json())
    assert steps["rebuild"] == ("failed", "1 stage picture(s) could not be drawn")
    assert {steps[k][0] for k in ("pdf", "xlsx", "analysis")} == {"skipped"}
    assert not (folder / "exports" / "session-full.pdf").exists() and not (folder / "exports" / "report.xlsx").exists()


def test_an_unknown_session_is_a_404(client, isolated_env: Path) -> None:
    assert client.post("/api/sessions/nope/wrap-up").status_code == 404
