"""One terminate-wait-kill for a child process this app spawned."""
from __future__ import annotations

import subprocess


def stop_process(proc: subprocess.Popen, timeout_s: float) -> None:
    """Ask ``proc`` to end, then kill it if it is still alive after ``timeout_s``."""
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
