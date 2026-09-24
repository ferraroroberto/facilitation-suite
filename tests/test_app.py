"""The shell: pages, build identity, error envelope, config fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import config as config_mod


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
