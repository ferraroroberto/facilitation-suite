"""Two saves of ``config.json`` that overlap both survive: each one's read-modify-write
runs whole before the next one starts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src import settings as settings_file

# Long enough for a second, unqueued save to reach its own read; a queued one never does,
# so this is also what each case costs when the saves are serialised.
OVERLAP_WAIT_S = 0.5

APPEARANCE = ("PUT", "/api/settings/appearance", {"appearance": "dark"})
MUSIC = ("PUT", "/api/settings/defaults", {"music": {"fade_in_s": 7}})
LETTERING = ("PUT", "/api/settings/defaults", {"stage": {"font": {"family": "Georgia"}}})


def _overlap(client: TestClient, monkeypatch: pytest.MonkeyPatch, first: tuple, second: tuple) -> None:
    """Send ``first``, hold it between its read and its write, and send ``second`` meanwhile."""
    real_read = settings_file._read_raw
    first_read, second_read = threading.Event(), threading.Event()

    def read() -> dict[str, Any]:
        raw = real_read()
        if first_read.is_set():
            second_read.set()
        else:
            first_read.set()
            second_read.wait(OVERLAP_WAIT_S)
        return raw

    monkeypatch.setattr(settings_file, "_read_raw", read)
    statuses: list[int] = []

    def send(method: str, url: str, body: Any) -> None:
        statuses.append(client.request(method, url, json=body).status_code)

    held = threading.Thread(target=send, args=first)
    held.start()
    assert first_read.wait(5), "the first save never read the file"
    send(*second)
    held.join(5)
    assert statuses == [200, 200]


def _saved(root: Path) -> dict[str, Any]:
    return json.loads((root / "config.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("first", "kept"),
    [
        (("PUT", "/api/settings", {"stage_click": {"advance": False}}), lambda c: c["stage_click"]["advance"] is False),
        (("POST", "/api/settings/remote/token", None), lambda c: bool(c["remote"]["token"])),
        (("DELETE", "/api/settings/remote/token", None), lambda c: c["remote"]["token"] == ""),
        (MUSIC, lambda c: c["defaults"]["music"]["fade_in_s"] == 7),
    ],
    ids=["settings", "new-remote-token", "remote-off", "defaults"],
)
def test_a_save_overlapping_another_keeps_both(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, first: tuple, kept: Any
) -> None:
    _overlap(client, monkeypatch, first, APPEARANCE)
    saved = _saved(isolated_env)
    assert kept(saved) and saved["appearance"] == "dark"
    assert client.app.state.config.appearance == "dark"


def test_two_defaults_saves_keep_both_halves(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defaults are written as one whole block, so the block is built from the file the save replaces."""
    _overlap(client, monkeypatch, MUSIC, LETTERING)
    saved = _saved(isolated_env)["defaults"]
    assert saved["music"]["fade_in_s"] == 7 and saved["stage"]["font"]["family"] == "Georgia"
