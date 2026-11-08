"""Atomic file writes: a reader sees the old file or the new one, never half of either.

The one place this app calls ``os.replace`` for a write. Each write goes to a
unique ``mkstemp`` sibling of the target, then ``replace_held`` swaps it in.
Session folders are synced by OneDrive while the app runs, and OneDrive holds a
file open for a moment while it uploads it; Windows refuses to replace an open
file ("Access is denied"), so the swap is retried a few times, briefly, before
it gives up. When the write or the swap raises, the temp is unlinked and the
error re-raised, so the old content stays intact and no orphan is left.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from src.write_target import outside_code

logger = logging.getLogger(__name__)

# Waits between tries when the target is held open (≈0.4 s in all).
REPLACE_WAITS = (0.02, 0.05, 0.1, 0.25)


def replace_held(tmp: str | Path, path: Path) -> None:
    """``os.replace``, tried again while another program (OneDrive) or thread holds ``path`` open."""
    for attempt, wait in enumerate(REPLACE_WAITS, start=1):
        try:
            os.replace(tmp, path)
        except PermissionError:
            time.sleep(wait)
            continue
        if attempt > 1:
            logger.info("ℹ️ %s was held open by another program — written on try %d", path.name, attempt)
        return
    os.replace(tmp, path)  # the last try raises what Windows says


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` as UTF-8 with LF line ends, creating the parent folder.

    Raises ``ValueError`` for a path under one of the app's code folders (``outside_code``).
    """
    target = outside_code(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        replace_held(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
