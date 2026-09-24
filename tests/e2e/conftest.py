"""Playwright e2e fixtures — a disposable webapp per session, never the live :8449.

``webapp`` boots uvicorn on a free loopback port with ``FS_CONFIG_PATH`` → a
temp copy of the sample config (OBS and the chat reader off),
``FS_LEDGER_PATH`` → a temp ledger and ``FS_DATA_DIR`` → a temp data dir, so a
run never reads or writes the real config, ledger or session folders.
``FS_E2E_LIVE=1`` is the one loudly-named opt-in to act on the live instance
(read-only; the vendored guard refuses otherwise).

Screenshots from a story go to ``docs/screenshots`` through ``shot()`` so the
repo carries the proof; fixtures are synthetic only.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import write_test_config
from tests.e2e._e2e_live_guard import require_disposable_instance

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOTS_DIR = REPO_ROOT / "docs" / "screenshots"
LIVE_PORT = 8449
LIVE_ENV = "FS_E2E_LIVE"
LOOP_FACTORY = "app.webapp.event_loop:selector_loop_factory"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Instance:
    def __init__(self, base_url: str, root: Path) -> None:
        self.base_url = base_url
        self.root = root


def stop_instance(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    log = getattr(proc, "fs_log", None)
    if log is not None:
        log.close()


def boot_instance(root: Path, **config: object) -> tuple[subprocess.Popen, Instance]:
    """Start a disposable server under ``root``; returns (process, instance)."""
    root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    write_test_config(root / "config.json", port=port, session_root=str(root / "sessions"), **config)
    env = os.environ.copy()
    env.update(
        FS_CONFIG_PATH=str(root / "config.json"),
        FS_LEDGER_PATH=str(root / "sessions.local.yaml"),
        FS_DATA_DIR=str(root / "data"),
        PYTHONUTF8="1",
    )
    log = open(root / "server.log", "wb")  # noqa: SIM115 — closed with the process
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.webapp.server:app", "--host", "127.0.0.1",
         "--port", str(port), "--loop", LOOP_FACTORY, "--log-level", "warning"],
        cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
    )
    proc.fs_log = log  # type: ignore[attr-defined] — closed by stop_instance()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"e2e webapp exited early; see {root / 'server.log'}")
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return proc, Instance(base, root)
        except OSError:
            time.sleep(0.25)
    stop_instance(proc)
    raise RuntimeError(f"e2e webapp did not answer /healthz within 30 s; see {root / 'server.log'}")


@pytest.fixture(scope="session")
def webapp() -> Iterator[Instance]:
    if require_disposable_instance(LIVE_PORT, LIVE_ENV):
        yield Instance(f"http://127.0.0.1:{LIVE_PORT}", Path(tempfile.gettempdir()))
        return
    with tempfile.TemporaryDirectory(prefix="fs-e2e-", ignore_cleanup_errors=True) as tmp:
        proc, inst = boot_instance(Path(tmp))
        try:
            yield inst
        finally:
            stop_instance(proc)


@pytest.fixture
def shots() -> Path:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SHOTS_DIR


def shot(page, path: Path) -> None:
    """Deterministic capture: no caret, no animations, fonts settled."""
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(path), animations="disabled", caret="hide")
