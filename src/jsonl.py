"""The one JSONL reader for the session's append-only logs (``live/chat.jsonl``,
``live/events.jsonl``): the live chat and the Results tab read them the same way."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every parseable line of a JSONL file; a damaged line is skipped and logged."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.error("❌ %s unreadable (%s)", path.name, exc)
        return []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("⚠️ %s line %d is not JSON — skipped", path.name, n)
    return out
