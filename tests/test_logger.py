"""ResilientRotatingFileHandler: keep writing when an outside process holds the log open (#100).

#37 gave every *process this app owns* one writer per file, but an outside process — a stray
old server, an editor, a ``tail`` — can still hold ``facilitation-suite.log`` open. Windows then
refuses the rename a rollover needs (WinError 32 / ``PermissionError``) regardless of how
disciplined this app's own processes are, and stock ``RotatingFileHandler`` drops every record
from then on (proven live: a leftover server from another session held the file since 2026-09-26).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src import config as config_mod
from src.logger import ResilientRotatingFileHandler
from src.no_window import NO_WINDOW

# A second process that opens the log file for reading only — no special share mode needed:
# a plain read-open on Windows already denies the delete/rename share a rollover needs, which is
# exactly what made the live file (#100) get stuck under a stray leftover process.
_HOLD_OPEN = """
import os, sys, time
path, go, done = sys.argv[1], sys.argv[2], sys.argv[3]
f = open(path, "rb")
open(go, "w").close()
while not os.path.exists(done):
    time.sleep(0.05)
f.close()
"""


def _make_handler(path: Path, **kwargs: object) -> ResilientRotatingFileHandler:
    handler = ResilientRotatingFileHandler(path, maxBytes=2_000, backupCount=3, encoding="utf-8", **kwargs)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def test_records_survive_and_rotation_resumes_when_an_outside_process_holds_the_file_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "held.log"
    path.write_text("", encoding="utf-8")
    go, done = tmp_path / "go", tmp_path / "done"
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD_OPEN, str(path), str(go), str(done)],
        cwd=config_mod.PROJECT_ROOT, creationflags=NO_WINDOW,
    )
    try:
        _wait_for(go)

        # retry_after_records=5 keeps the test fast: a handful of post-release records is enough
        # to prove the retry fires, instead of waiting out the real production interval.
        handler = _make_handler(path, retry_after_records=5, retry_after_seconds=999)
        log = logging.getLogger("test-logger-100-blocked")
        log.handlers = [handler]
        log.setLevel(logging.INFO)
        log.propagate = False

        n = 400  # ~50 bytes/record → well past maxBytes=2000, several blocked rollover attempts
        with caplog.at_level(logging.INFO, logger="src.logger"):
            for i in range(n):
                log.info("line-%04d %s", i, "x" * 40)
        handler.flush()

        # every record landed — none dropped despite the file being held open past maxBytes
        text = path.read_text(encoding="utf-8", errors="replace")
        for i in range(n):
            assert f"line-{i:04d}" in text, f"record {i} was dropped"
        assert handler._rotation_blocked is True
        assert not (tmp_path / "held.log.1").exists(), "rotation must not have succeeded while blocked"

        # exactly one warning for the whole blocked episode, not one per blocked emit()
        warnings = [r for r in caplog.records if r.name == "src.logger" and r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "rotation blocked" in warnings[0].getMessage()

        # release the outside handle — rotation must resume
        done.touch()
        proc.wait(timeout=30)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="src.logger"):
            for i in range(n, n + 20):
                log.info("line-%04d %s", i, "x" * 40)
        handler.flush()

        assert (tmp_path / "held.log.1").exists(), "rotation did not resume once the handle closed"
        assert handler._rotation_blocked is False
        resumed = [r for r in caplog.records if r.name == "src.logger" and "rotation resumed" in r.getMessage()]
        assert resumed, "no breadcrumb logged when rotation recovered"

        kept = "".join(p.read_text(encoding="utf-8", errors="replace") for p in tmp_path.glob("held.log*"))
        for i in range(n + 20):
            assert f"line-{i:04d}" in kept, f"record {i} lost across rotation"
        handler.close()
    finally:
        if not done.exists():
            done.touch()
        if proc.poll() is None:
            proc.wait(timeout=10)
