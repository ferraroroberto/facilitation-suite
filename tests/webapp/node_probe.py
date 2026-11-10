"""Run a dependency-free ES module under Node and read back the JSON it prints (the declared-state tests share it)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW


def probe_module(module: Path, script: str) -> dict:
    """``script`` imports the module from ``process.argv[1]`` and prints one JSON object; skips without Node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed (the gate's static ratchet needs it too)")
    run = subprocess.run([node, "--input-type=module", "-e", script, module.as_uri()],
                         capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
                         creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)
