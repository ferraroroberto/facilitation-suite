"""Hermetic unit-test fixtures: every test runs against temp config, ledger and data dirs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CONFIG = REPO_ROOT / "config" / "config.sample.json"


def write_test_config(path: Path, **overrides: object) -> Path:
    """A copy of the committed sample with ``overrides`` applied (OBS and the reader off)."""
    cfg = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))
    cfg["obs"]["enabled"] = False
    cfg["reader"]["enabled"] = False
    cfg.update(overrides)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point config, ledger and data at a temp dir so no test touches real files."""
    root = tmp_path / "env"
    root.mkdir()
    write_test_config(root / "config.json", session_root=str(root / "sessions"))
    monkeypatch.setenv("FS_CONFIG_PATH", str(root / "config.json"))
    monkeypatch.setenv("FS_LEDGER_PATH", str(root / "sessions.local.yaml"))
    monkeypatch.setenv("FS_DATA_DIR", str(root / "data"))
    yield root


@pytest.fixture
def client(isolated_env: Path):
    """A TestClient over a freshly built app (loopback client address)."""
    from fastapi.testclient import TestClient

    from app.webapp.server import create_app

    with TestClient(create_app(), client=("127.0.0.1", 50000)) as c:
        yield c


def paired_remote(app, host: str = "100.64.0.9"):  # noqa: ANN001, ANN201 — FastAPI app → TestClient
    """A TestClient from another device that holds the phone-remote token (the app gets one first)."""
    from fastapi.testclient import TestClient

    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        pc.post("/api/settings/remote/token")
    return TestClient(app, client=(host, 5000), headers={"Authorization": f"Bearer {app.state.config.remote.token}"})
