"""The load bots' target guard (#56, #82): never the live app, never the live public player."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import write_test_config


@pytest.fixture
def bots(isolated_env: Path):  # noqa: ANN201 — the script module
    import importlib

    write_test_config(Path(os.environ["FS_CONFIG_PATH"]), session_root=str(isolated_env / "sessions"),
                      quiz={"public_port": 0, "public_url": "https://tower.example.ts.net:10000"})
    (isolated_env / "cloudflared.yml").write_text(
        "ingress:\n  - hostname: quiz.example.test\n    service: http://127.0.0.1:8451\n"
        "  - service: http_status:404\n", encoding="utf-8")
    return importlib.import_module("scripts.quiz_bots")


@pytest.mark.parametrize("base", ["https://quiz.example.test/play", "https://QUIZ.example.test",
                                  "https://tower.example.ts.net:10000/play"])
def test_the_live_public_player_is_refused(bots, base: str) -> None:
    with pytest.raises(SystemExit):
        bots.parse_args(["--player-base", base])


@pytest.mark.parametrize("base", ["https://quiz-bots.example.test/play", "https://tower.example.ts.net:10000/fs-bots",
                                  "http://127.0.0.1:18461/play"])
def test_a_temporary_hostname_or_path_is_allowed(bots, base: str) -> None:
    assert bots.parse_args(["--player-base", base]).player_base == base


def test_the_live_ports_are_refused(bots) -> None:
    for argv in (["--port", "8449"], ["--player-port", "8451"]):
        with pytest.raises(SystemExit):
            bots.parse_args(argv)
