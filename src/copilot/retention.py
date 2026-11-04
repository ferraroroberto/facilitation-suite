"""How long the copilot's recordings are kept (``copilot.recording_retention_days``).

The audio is the most sensitive thing the copilot writes, so a facilitator can have it removed after a
number of days. The default keeps everything: nothing is deleted until a number is set. Only the
``*.wav`` files directly in a session's ``live/copilot/`` are ever touched; the transcript, the hints
and the summary are left as they are.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DAY_S = 86400


def purge_recordings(session_folder: Path, days: int, now: Optional[float] = None) -> list[str]:
    """Remove the session's recordings last written more than ``days`` days ago; return their names.

    ``days`` of 0 keeps everything. A file that cannot be removed is logged and left, never raised: opening
    a session must not fail over an old recording.
    """
    if days <= 0:
        return []
    folder = session_folder / "live" / "copilot"
    if not folder.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - days * DAY_S
    removed: list[str] = []
    for wav in sorted(folder.glob("*.wav")):
        try:
            if not wav.is_file() or wav.stat().st_mtime >= cutoff:
                continue
            wav.unlink()
        except OSError as exc:
            logger.warning("⚠️ copilot: recording %s could not be removed (%s)", wav.name, exc)
            continue
        removed.append(wav.name)
        logger.info("ℹ️ copilot: removed recording %s — older than the %d-day retention", wav.name, days)
    return removed
