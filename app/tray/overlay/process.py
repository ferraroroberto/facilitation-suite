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

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class OverlayProcess:
    def __init__(self, server_url: Callable[[], str]) -> None:
        self.server_url = server_url  # read at each start: the address the server was last reached on
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running() or sys.platform != "win32":
                return
            url = self.server_url()
            cmd = [sys.executable, "-m", "app.tray.overlay", "--server", url, "--parent-pid", str(os.getpid())]
            self.proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=dict(os.environ, PYTHONUTF8="1"),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            logger.info("✅ overlay started (pid %s, %s)", self.proc.pid, url)

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
