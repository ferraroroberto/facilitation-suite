"""Check the app's chat record against the chat file Zoom saves at meeting end.

Zoom's saved chat (``meeting_saved_chat.txt``, auto-saved under the Zoom
folder in Documents) is the ground truth of what was said; ``chat.jsonl`` is
what the reader caught. The report says how many of Zoom's messages the app
has ("412 of 412 matched") and lists the ones it is missing — and the ones
only the app has.

Two layouts of the saved file are read (Zoom has used both):

    18:40:12 From Sofía L. to Everyone:
    \tperfeccionismo                      ← text on the following tab-indented line(s)

    18:40:12\t From  Sofía L. : perfeccionismo     ← older: text on the same line

Reactions ("Reacted to … with …") are not chat rows in the window the reader
reads, so they are counted apart, never as missing; a "Replying to …" quote
line is dropped from the reply's text.

Matching, in order: same text and sender, times within a minute (the panel
shows minutes, the file seconds). The reader drops emoji and shows the
facilitator as "You", so text is compared without symbols and an own message
matches any sender. Simulated messages are never in Zoom's file and are only
counted.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HEAD_TO = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s+From\s+(.+?)\s+to\s+(.+?)\s*:\s*(.*)$", re.IGNORECASE)
HEAD_OLD = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s+From\s+(.+?)\s*:\s?(.*)$", re.IGNORECASE)
REACTION = re.compile(r'^(Reacted to|Removed a) "', re.IGNORECASE)
REPLY = re.compile(r'^Replying to "', re.IGNORECASE)
PANEL_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])?$")
MAX_LISTED = 200


@dataclass
class SavedMessage:
    time: str  # HH:MM:SS
    sender: str
    to: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"time": self.time, "sender": self.sender, "to": self.to, "text": self.text}


def parse_saved_chat(text: str) -> list[SavedMessage]:
    """Zoom's saved chat text → messages in order (both layouts; unknown lines join the previous message)."""
    out: list[SavedMessage] = []
    for raw in text.replace("\r\n", "\n").lstrip("﻿").split("\n"):
        line = raw.rstrip()
        m = HEAD_TO.match(line.strip())
        if m:
            out.append(SavedMessage(m.group(1), m.group(2).strip(), m.group(3).strip(), m.group(4).strip()))
            continue
        m = HEAD_OLD.match(line.strip())
        if m:
            out.append(SavedMessage(m.group(1), m.group(2).strip(), "", m.group(3).strip()))
            continue
        if out and line.strip():
            body = line.strip()
            cur = out[-1]
            cur.text = f"{cur.text}\n{body}" if cur.text else body
    for msg in out:
        lines = msg.text.split("\n")
        if lines and REPLY.match(lines[0]):
            msg.text = "\n".join(lines[1:]).strip()
    return out


def norm_text(text: str) -> str:
    """Case- and space-insensitive, without symbols the reader never sees (emoji)."""
    kept = "".join(c for c in unicodedata.normalize("NFC", text)
                   if unicodedata.category(c) not in ("So", "Sk", "Cf", "Mn") or c.isalnum())
    return " ".join(kept.split()).casefold()


def norm_sender(sender: str) -> str:
    return " ".join(sender.split()).casefold()


def minutes_of(clock: str) -> Optional[int]:
    """"18:40:12" / "18:40" / "6:40 PM" → minutes since midnight."""
    parts = clock.strip().split(":")
    if len(parts) == 3:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None
    m = PANEL_TIME.match(clock.strip())
    if not m:
        return None
    h, mi, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ampm == "pm" and h != 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    return h * 60 + mi


def _close(a: Optional[int], b: Optional[int]) -> bool:
    if a is None or b is None:
        return True  # no time to compare: text and sender decide
    d = abs(a - b)
    return min(d, 24 * 60 - d) <= 1


def reconcile(saved: list[SavedMessage], chat: list[dict[str, Any]]) -> dict[str, Any]:
    reactions = [s for s in saved if REACTION.match(s.text)]
    messages = [s for s in saved if not REACTION.match(s.text)]
    recorded = [m for m in chat if m.get("source", "zoom") != "simulator"]
    simulated = len(chat) - len(recorded)
    used = [False] * len(recorded)
    keys = [(norm_text(m.get("text", "")), norm_sender(m.get("sender", "")), minutes_of(m.get("time", "")), bool(m.get("own")))
            for m in recorded]
    missing: list[SavedMessage] = []
    matched = 0
    start = 0  # messages arrive in order: search forward from the last match first
    for s in messages:
        text, sender, minute = norm_text(s.text), norm_sender(s.sender), minutes_of(s.time)
        found = None
        for j in [*range(start, len(recorded)), *range(0, start)]:
            if used[j]:
                continue
            t, who, mn, own = keys[j]
            if t == text and (own or who == sender) and _close(mn, minute):
                found = j
                break
        if found is None:
            missing.append(s)
            continue
        used[found] = True
        matched += 1
        start = found + 1
    extra = [m for j, m in enumerate(recorded) if not used[j]]
    return {
        "zoom_messages": len(messages),
        "reactions": len(reactions),
        "matched": matched,
        "missing": [s.as_dict() for s in missing[:MAX_LISTED]],
        "missing_count": len(missing),
        "extra": [{"time": m.get("time", ""), "sender": m.get("sender", ""), "text": m.get("text", "")} for m in extra[:MAX_LISTED]],
        "extra_count": len(extra),
        "recorded": len(recorded),
        "simulated": simulated,
    }


def reconcile_file(path: Path, chat: list[dict[str, Any]]) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    saved = parse_saved_chat(text)
    report = reconcile(saved, chat)
    report.update(file=path.name, folder=path.parent.name, checked_at=datetime.now(UTC).isoformat(timespec="seconds"))
    logger.info("ℹ️ reconciliation: %d of %d Zoom messages matched (%d missing, %d only in the app, %d reactions) — %s",
                report["matched"], report["zoom_messages"], report["missing_count"], report["extra_count"],
                report["reactions"], path.name)
    return report
