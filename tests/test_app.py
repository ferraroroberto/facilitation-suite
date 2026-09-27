"""The shell: pages, build identity, error envelope, config fallbacks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from src import config as config_mod
from src import logger as logger_mod
from src.no_window import NO_WINDOW


def test_version_reports_build_identity(client) -> None:
    body = client.get("/api/version").json()
    assert body["app"] == "facilitation-suite"
    assert body["git_sha"]
    assert body["schema_version"] == 1


def test_healthz(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/presenter", "/stage"])
def test_pages_are_served_no_cache(client, path: str) -> None:
    res = client.get(path)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert "<!--SPRITE-->" not in res.text


def test_index_inlines_the_sprite(client) -> None:
    html = client.get("/").text
    assert 'id="i-presentation"' in html


def test_unknown_route_uses_the_error_envelope(client) -> None:
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_static_is_revalidated(client) -> None:
    res = client.get("/static/css/tokens.css")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"


def test_config_defaults_on_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FS_CONFIG_PATH", str(tmp_path / "absent.json"))
    cfg = config_mod.load_config()
    assert cfg.port == 8449
    assert cfg.source == "defaults"


def test_config_wrong_type_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"port": "not-a-port", "obs": {"port": 4460, "enabled": "yes"}}), encoding="utf-8")
    monkeypatch.setenv("FS_CONFIG_PATH", str(path))
    cfg = config_mod.load_config()
    assert cfg.port == 8449
    assert cfg.obs.port == 4460
    assert cfg.obs.enabled is True


def test_log_follows_fs_data_dir(tmp_path: Path) -> None:
    """A second instance (a test run, a scratch server) must not write into the tray's log (#26)."""
    marker = f"log-probe-{uuid.uuid4().hex}"
    data = tmp_path / "data"
    script = ("import logging; from src.logger import configure_logging; "
              f"configure_logging(); logging.getLogger('probe').info({marker!r})")
    env = dict(os.environ, FS_DATA_DIR=str(data), PYTHONUTF8="1")
    subprocess.run([sys.executable, "-c", script], cwd=config_mod.PROJECT_ROOT, env=env, check=True,
                   capture_output=True, timeout=60, creationflags=NO_WINDOW)
    assert marker in (data / "logs" / "facilitation-suite.log").read_text(encoding="utf-8")
    repo_log = config_mod.PROJECT_ROOT / "data" / "logs" / "facilitation-suite.log"
    assert not repo_log.is_file() or marker not in repo_log.read_text(encoding="utf-8", errors="replace")


def test_log_defaults_to_the_repo_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FS_DATA_DIR", raising=False)
    assert logger_mod.default_log_file() == config_mod.PROJECT_ROOT / "data" / "logs" / "facilitation-suite.log"
