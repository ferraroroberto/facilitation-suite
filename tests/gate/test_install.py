"""``setup.bat`` on this app: what ``src/installer.py`` does with the ``[install]`` table of ``.fleet.toml``.

A fresh checkout gets its configs, a configured one keeps every value, and adding a missing key never changes
how the app behaves. The installer runs for real over a temp checkout; only its subprocesses (venv, pip,
playwright and the checklist probes) are stood in for.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src import defaults, features, installer
from src.config import CONFIG_SAMPLE_PATH, AppConfig, _build, profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_INPUTS = (".fleet.toml", "requirements.txt", "config/config.sample.json", ".env.example")


# The oldest Python the app takes (the fleet's baseline): a PC with only this one must install.
OLDEST_PYTHON = "3.12"


def _runner(command: list[str], **_: Any) -> subprocess.CompletedProcess:
    asks_version = installer.PYTHON_VERSION_SNIPPET in command
    return subprocess.CompletedProcess(command, 0, stdout=f"{OLDEST_PYTHON}\n" if asks_version else "")


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    for rel in TRACKED_INPUTS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, root / rel)
    python = installer.venv_python(root)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")  # an existing .venv: nothing creates one in a test
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _run(root: Path, *, what_if: bool = False) -> installer.Report:
    return installer.run(root, what_if=what_if, runner=_runner)


def test_a_fresh_checkout_gets_its_configs_and_a_second_run_changes_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    before = _files(root)

    assert _run(root, what_if=True).changes
    assert _files(root) == before  # -WhatIf writes nothing

    first = _run(root)
    assert not first.failed
    assert (root / "config/config.json").read_bytes() == (root / "config/config.sample.json").read_bytes()
    assert (root / ".env").read_bytes() == (root / ".env.example").read_bytes()
    # Their templates hold placeholder entries the app would read as real.
    assert not (root / "sessions.local.yaml").exists() and not (root / "webapp").exists()
    assert {name for _, name, _ in first.required} >= {"config/config.json", ".env", f"Python {OLDEST_PYTHON}"}

    installed = _files(root)
    second = _run(root)
    assert not second.failed and not second.changes
    assert _files(root) == installed


def test_a_configured_pc_keeps_its_values_and_gains_only_the_missing_keys(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    mine = {"session_root": "D:/mine", "obs": {"password": "synthetic"}, "features": {"music": False}, "extra": 1}
    config = root / "config/config.json"
    config.write_text(json.dumps(mine), encoding="utf-8")
    original = config.read_bytes()
    (root / ".env").write_text("SPOTIFY_CLIENT_ID=synthetic\n", encoding="utf-8")
    ledger = root / "sessions.local.yaml"
    ledger.write_text("sessions: []\n", encoding="utf-8")

    assert not _run(root).failed

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert (saved["session_root"], saved["obs"]["password"], saved["extra"]) == ("D:/mine", "synthetic", 1)
    assert saved["features"]["music"] is False and saved["obs"]["port"] == 4455 and saved["port"] == 8449
    [backup] = (root / "config").glob("config.json.bak-*")
    assert backup.read_bytes() == original
    assert (root / ".env").read_text(encoding="utf-8") == "SPOTIFY_CLIENT_ID=synthetic\n"
    assert ledger.read_text(encoding="utf-8") == "sessions: []\n"


def test_a_top_up_keeps_an_older_obs_or_reader_switch_off(tmp_path: Path) -> None:
    """An older config switches a block off with ``obs.enabled`` / ``reader.enabled``; an added
    ``features`` value would win over it."""
    root = _checkout(tmp_path)
    older = {"obs": {"enabled": False}, "reader": {"enabled": False}}
    (root / "config/config.json").write_text(json.dumps(older), encoding="utf-8")

    assert not _run(root).failed

    chosen = features.resolve(json.loads((root / "config/config.json").read_text(encoding="utf-8")))
    assert chosen["obs"] is False and chosen["chat_reader"] is False


def test_a_key_the_sample_adds_never_changes_behaviour() -> None:
    """A top-up copies the sample's value into a config that ran on the code default, so the two must agree."""
    raw = json.loads(CONFIG_SAMPLE_PATH.read_text(encoding="utf-8"))
    sample, code = _build(AppConfig, raw), AppConfig()
    spelled_out = {"profiles", "defaults", "features"}  # compared below as the app resolves them
    for item in dataclasses.fields(AppConfig):
        if item.name not in spelled_out:
            assert getattr(sample, item.name) == getattr(code, item.name), item.name
    assert profiles(sample) == profiles(code)
    assert defaults.load(sample) == defaults.load(code)
    assert features.resolve(raw) == features.DEFAULTS
