"""The build readout's restart route is mounted behind the phone-remote gate.

``src/self_restart.py`` is vendored verbatim and tested in project-scaffolding; this covers the
app-side wiring. No test posts a same-origin restart through the real app, because that would run
the real ``tray.bat --restart``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.webapp.auth import require_authenticated
from src.self_restart import TRAY_RESTART, RestartTarget, restart_router

PC = ("127.0.0.1", 50000)
PHONE = ("100.64.0.9", 5000)
ORIGIN = {"Origin": "http://testserver"}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def app(isolated_env: Path, monkeypatch: pytest.MonkeyPatch):
    from app.webapp.routers import settings as settings_router
    from app.webapp.server import create_app

    monkeypatch.setattr(settings_router, "cert_hostname", lambda: "pc.example.ts.net")
    return create_app()


def _paired_phone(app) -> tuple[str, str]:
    with TestClient(app, client=PC, base_url="https://127.0.0.1:8449") as pc:
        link = pc.post("/api/settings/remote/token").json()["remote"]["link"]
    return link.split("token=")[1], "https://pc.example.ts.net:8449"


def test_this_pc_sees_the_tray_recipe_as_the_only_target(app) -> None:
    with TestClient(app, client=PC) as pc:
        res = pc.get("/api/restart")
    assert res.status_code == 200
    assert [t["id"] for t in res.json()["targets"]] == ["app"]


def test_an_unpaired_phone_cannot_read_or_start_a_restart(app) -> None:
    with TestClient(app, client=PHONE) as phone:
        assert phone.get("/api/restart").status_code == 401
        assert phone.post("/api/restart", json={"target": "app"}, headers=ORIGIN).status_code == 401


def test_a_paired_phone_reads_it_and_a_cross_origin_post_is_refused(app) -> None:
    token, base = _paired_phone(app)
    with TestClient(app, client=PHONE, base_url=base) as phone:
        assert phone.get("/api/restart", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        res = phone.post(
            "/api/restart", json={"target": "app"},
            headers={"Authorization": f"Bearer {token}", "Origin": "https://elsewhere.example"},
        )
    assert res.status_code == 403


def test_the_route_refuses_when_the_gate_is_missing(tmp_path: Path) -> None:
    bare = FastAPI()
    bare.include_router(restart_router(
        targets=[RestartTarget("app", "Restart app", TRAY_RESTART)],
        status_dir=tmp_path / "restart", guard=require_authenticated, root=tmp_path,
        spawn=lambda spec, root: pytest.fail("a restart started without auth"),
    ))
    res = TestClient(bare).post("/api/restart", json={"target": "app"}, headers=ORIGIN)
    assert res.status_code == 401


def test_the_recipe_runs_the_repos_own_tray_bat() -> None:
    assert TRAY_RESTART[-2:] == (".\\tray.bat", "--restart")
    assert (ROOT / "tray.bat").is_file()
