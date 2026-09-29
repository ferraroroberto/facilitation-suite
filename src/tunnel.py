"""The Cloudflare named tunnel that publishes the quiz player (#82).

``webapp/cloudflared.yml`` (gitignored; template ``webapp/cloudflared.sample.yml``)
maps the public hostname (``quiz.<domain>``) to the player listener on
``127.0.0.1:<quiz.public_port>``. When that file exists, the tray runs
``cloudflared tunnel --config webapp/cloudflared.yml --metrics 127.0.0.1:8452 run``
as an **owned-and-cycled** child (project-scaffolding ``docs/windows-tray.md``):

- it lives in the tray's process tree, so ``tray.bat --restart``'s subtree kill
  takes it down with the tray, and ``Quit`` stops it;
- before spawning, the tray ends any cloudflared still running **this** config
  (an orphan whose tray died) — matched by its command line, never by name, so
  the fleet's other tunnels (their own configs) are never touched;
- if it exits, the tray starts it again (30 s, doubling to 5 min).

``--metrics`` puts cloudflared's own readiness endpoint on a fixed loopback
port: ``GET /ready`` answers 200 once the tunnel holds an edge connection and
503 while it has none. The public-link check (``src/quiz/reach.py``) reads it to
tell *cloudflared is not running* from *the edge does not answer*.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import yaml

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "webapp" / "cloudflared.yml"
METRICS_HOST = "127.0.0.1"
METRICS_PORT = 8452  # next to the player listener's 8451; 8450 is parking-manager's
READY_TIMEOUT_S = 2.0
RESPAWN_FIRST_S = 30.0
RESPAWN_MAX_S = 300.0
LOG_ROTATE_BYTES = 5_000_000
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def config_path() -> Path:
    """The tunnel config (looked up at call time, so a test can point it elsewhere)."""
    return CONFIG_PATH


def hostnames(path: Optional[Path] = None) -> set[str]:
    """Every ``ingress[].hostname`` in the tunnel config; empty when it is missing or unreadable."""
    path = path or config_path()
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("⚠️ tunnel: could not read %s (%s)", path, exc)
        return set()
    return {str(e["hostname"]).strip().lower() for e in data.get("ingress") or []
            if isinstance(e, dict) and e.get("hostname")}


def ready(timeout: float = READY_TIMEOUT_S) -> Optional[str]:
    """``None`` when cloudflared reports an edge connection on its metrics port, else why not."""
    url = f"http://{METRICS_HOST}:{METRICS_PORT}/ready"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body: dict[str, Any] = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:  # 503: running, but no edge connection
        return f"cloudflared is running but has no connection to Cloudflare (HTTP {exc.code})"
    except (OSError, ValueError) as exc:
        return f"cloudflared is not running ({METRICS_HOST}:{METRICS_PORT} does not answer: {type(exc).__name__})"
    if not body.get("readyConnections"):
        return "cloudflared is running but has no connection to Cloudflare"
    return None


def problem(host: str) -> Optional[str]:
    """Why the tunnel cannot carry ``host`` right now; ``None`` when it can, or when ``host`` is not ours to publish."""
    if host.lower() not in hostnames():
        return None  # another ingress (e.g. Tailscale Funnel): nothing of ours to check
    return ready()


# ------------------------------------------------------------------ the process (tray)


def _own_orphans(path: Path) -> list[int]:
    """PIDs of cloudflared processes whose command line names ``path`` (Windows; ``[]`` elsewhere)."""
    if sys.platform != "win32":
        return []
    literal = str(path).replace("'", "''")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
              f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{literal}') }} | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, encoding="oem", errors="replace",
                             timeout=20, creationflags=NO_WINDOW, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("⚠️ tunnel: could not list cloudflared processes (%s)", exc)
        return []
    return [int(line) for line in out.stdout.split() if line.strip().isdigit()]


class Tunnel:
    """The tray's cloudflared child: start, keep running, stop (see the module docstring)."""

    def __init__(self, path: Optional[Path] = None, log_file: Optional[Path] = None) -> None:
        self.path = path or config_path()
        self.log_file = log_file
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def command(self, binary: str) -> list[str]:
        cmd = [binary, "tunnel", "--config", str(self.path), "--metrics", f"{METRICS_HOST}:{METRICS_PORT}"]
        if self.log_file is not None:
            cmd += ["--logfile", str(self.log_file)]
        return cmd + ["run"]

    def start(self) -> bool:
        """Spawn cloudflared; ``False`` (logged) when there is no config or no binary."""
        with self._lock:
            if self._stop.is_set() or self.running():
                return self.running()
            if not self.path.exists():
                logger.warning("⚠️ tunnel: no %s — the quiz player is not published through Cloudflare "
                               "(copy webapp/cloudflared.sample.yml; README \"Quiz player (public)\")", self.path)
                return False
            binary = shutil.which("cloudflared")
            if binary is None:
                logger.error("❌ tunnel: cloudflared is not on PATH (winget install Cloudflare.cloudflared) — "
                             "the quiz player is not published")
                return False
            for pid in _own_orphans(self.path):
                logger.warning("⚠️ tunnel: ending an orphaned cloudflared on this config (pid %d)", pid)
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                               creationflags=NO_WINDOW, check=False)
            self._rotate_log()
            self.proc = subprocess.Popen(self.command(binary), cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=NO_WINDOW)
            logger.info("✅ tunnel: cloudflared started (pid %d, %s, readiness on %s:%d/ready)",
                        self.proc.pid, self.path.name, METRICS_HOST, METRICS_PORT)
            return True

    def supervise(self) -> None:
        """Start, then restart cloudflared whenever it exits, until ``stop()`` (run it in a thread)."""
        if not self.start():
            return
        delay = RESPAWN_FIRST_S
        while not self._stop.wait(RESPAWN_FIRST_S):
            proc = self.proc
            if proc is None or proc.poll() is None:
                delay = RESPAWN_FIRST_S
                continue
            logger.warning("⚠️ tunnel: cloudflared exited (code %s) — starting it again in %.0f s "
                           "(its log: %s)", proc.returncode, delay, self.log_file)
            if self._stop.wait(delay):
                return
            self.start()
            delay = min(delay * 2, RESPAWN_MAX_S)

    def stop(self) -> None:
        """Stop cloudflared (terminate, then kill after 5 s); no respawn afterwards."""
        self._stop.set()
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("👋 tunnel: cloudflared stopped")

    def _rotate_log(self) -> None:
        log = self.log_file
        if log is None:
            return
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            if log.exists() and log.stat().st_size > LOG_ROTATE_BYTES:
                log.replace(log.with_suffix(".log.1"))
        except OSError as exc:
            logger.debug("tunnel log rotation failed: %s", exc)
