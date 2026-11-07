"""Start and stop the overlay process (``python -m app.tray.overlay``) from the server.

The server owns its lifetime: it starts with Copilot, or when Settings asks for a sample, and stops
when Copilot goes off or the server shuts down. It is given the server's PID so it also exits when
the server dies, and one runs at a time.
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

from src.logger import log_path
from src.no_window import NO_WINDOW
from src.write_target import outside_code

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # `-m app.tray.overlay` resolves only from the folder holding `app/`
# A child that import-fails or cannot make its window dies within this; one still up after it is running.
START_GRACE_S = 1.5
STDERR_TAIL_CHARS = 600


class OverlayProcess:
    module = "app.tray.overlay"

    def __init__(self, server_url: Callable[[], str]) -> None:
        self.server_url = server_url  # read at each start: the address the server was last reached on
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        # Its stderr: the child dies before its own log exists when it cannot even import.
        self._stderr_file = log_path("overlay-stderr")

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        """Make sure one overlay is up; True when it is, False when it could not be (the reason is logged)."""
        with self._lock:
            if sys.platform != "win32":
                return False
            if self.running():
                return True
            if self.proc is not None:
                logger.warning("⚠️ overlay had exited (code %s) — starting it again", self.proc.returncode)
                self.proc = None
            url = self.server_url()
            cmd = [sys.executable, "-m", self.module, "--server", url, "--parent-pid", str(os.getpid())]
            target = outside_code(self._stderr_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as err:
                self.proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=dict(os.environ, PYTHONUTF8="1"),
                                             stdout=subprocess.DEVNULL, stderr=err, creationflags=NO_WINDOW)
            try:
                code = self.proc.wait(timeout=START_GRACE_S)
            except subprocess.TimeoutExpired:
                logger.info("✅ overlay started (pid %s, %s)", self.proc.pid, url)
                return True
            logger.warning("⚠️ overlay exited at once (code %s) — %s", code, self._stderr_tail())
            self.proc = None
            return False

    def _stderr_tail(self) -> str:
        try:
            text = self._stderr_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return "no error output"
        return text[-STDERR_TAIL_CHARS:] or "no error output"

    def stop(self) -> None:
        with self._lock:
            if self.proc is None:
                return
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                logger.info("ℹ️ overlay stopped")
            self.proc = None
