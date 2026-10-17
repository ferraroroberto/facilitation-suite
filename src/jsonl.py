"""The session's append-only logs (``live/chat.jsonl``, ``live/events.jsonl``,
``live/quiz.jsonl``): one reader, so the live chat and the Results tab read them
the same way, and one appender, so a failed write is handled the same way in each."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
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


class JsonlAppender:
    """Appends records to one session log, keeping what a failed write could not store.

    Records a write could not store stay pending and go out first with the next
    append, so a passing failure (OneDrive holding the file while it uploads)
    loses nothing and writes nothing twice. ``failed`` and ``written`` are told
    each outcome by the file's name (the live hub's ``write_failed`` and
    ``write_ok``), so the presenter's warning comes and goes with the file.
    """

    def __init__(self, name: str, failed: Callable[[str, OSError], None], written: Callable[[str], Any]) -> None:
        self.name = name
        self.failed, self.written = failed, written
        self.pending: list[dict[str, Any]] = []

    def append(self, path: Path, records: list[dict[str, Any]]) -> bool:
        """Write what is pending, then ``records``; False when the write failed (all of it kept)."""
        self.pending.extend(records)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self.pending))
        except OSError as exc:
            self.failed(self.name, exc)
            return False
        self.pending = []
        self.written(self.name)
        return True

    def forget(self) -> None:
        """The run the file belongs to is over (another session, a reset): drop what was never written."""
        if self.pending:
            logger.warning("⚠️ %d record(s) of %s were never written — dropped with their run", len(self.pending), self.name)
        self.pending = []
