"""Start and stop the chat reader process from the server.

The server owns the reader's lifetime: it starts when a session goes live
(when the config enables it), on the presenter's chip, or from the Sessions
readiness "Test"; it passes its own PID so the reader exits when the server
does (a restarted server never finds two readers posting). One reader at a
time — starting the simulator stops the Zoom reader and vice versa. The
reader reads the source chosen when it starts (``reader.source``, the
presenter's Zoom · Teams · Meet switch, #239), passed as ``--source``; starting on
another source than the running reader's restarts it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from src.chat.reader import SOURCES
from src.config import data_dir
from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ReaderProcess:
    def __init__(self, server_url: str, source: Callable[[], str] = lambda: "zoom") -> None:
        self.server_url = server_url
        self.source = source  # the chosen chat source, read at each start
        self.proc: Optional[subprocess.Popen] = None
        self.mode: Optional[str] = None
        self._lock = threading.Lock()

    @staticmethod
    def stop_file() -> Path:
        return data_dir() / "chat-reader.stop"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, simulate: Optional[str] = None, answers: Optional[list[str]] = None) -> str:
        """Start the reader on the chosen source (or a simulation); returns what is running."""
        with self._lock:
            chosen = self.source()
            want = "simulator" if simulate else (chosen if chosen in SOURCES else "zoom")
            if self.running() and self.mode == want and not simulate:
                return want
            self._stop_locked()
            stop = self.stop_file()
            stop.unlink(missing_ok=True)
            cmd = [sys.executable, "-m", "src.chat.reader", "--server", self.server_url,
                   "--parent-pid", str(os.getpid()), "--stop-file", str(stop)]
            if simulate:
                cmd += ["--simulate", simulate]
                if answers:
                    cmd += ["--answers", ",".join(a.replace(",", " ") for a in answers)]
            else:
                cmd += ["--source", want]
            env = dict(os.environ, PYTHONUTF8="1")
            self.proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            self.mode = want
            logger.info("✅ chat reader started (%s, pid %s)", f"simulator {simulate}" if simulate else want, self.proc.pid)
            return want

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            # Ask first (a clean exit restores the screen-reader flag), then force.
            stop = self.stop_file()
            try:
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.write_text(str(self.proc.pid), encoding="utf-8")
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            stop.unlink(missing_ok=True)
            logger.info("ℹ️ chat reader stopped (%s)", self.mode)
        self.proc, self.mode = None, None
