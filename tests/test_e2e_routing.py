"""This repo's own e2e routing (`.fleet.toml [e2e]`): a `src/` change runs the browser suite (#171).

Every e2e instance boots the app, which imports `src/`, so a diff that only touches `src/` can
break the browser flows. The vendored classifier is tested upstream; this pins only the repo's
declared rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import classify_e2e  # noqa: E402


@pytest.mark.parametrize(
    "path",
    [
        "src/tunnel.py",
        "src/quiz/reach.py",
        "src/__init__.py",
        "src/obs/client.py",
    ],
)
def test_src_change_routes_to_full(path: str) -> None:
    config = classify_e2e.load_config(REPO_ROOT / ".fleet.toml")
    routing = classify_e2e.classify([path], config)
    assert routing.tier == "full", routing.reasons
