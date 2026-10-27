"""Zoom chat rows → messages, and snapshot → new messages (pure, unit-tested).

A message row's accessible name is ``"<Sender>, , <text>, <HH:MM>, "``. The
text itself may contain commas (``"Sevilla, España"``), so: the first field
is the sender, the last non-empty field is the time, and everything between
(minus the empty second field) is the text. System rows (``"Today, "``,
``"13:58 - meeting started"``) have no time field in that shape and are
dropped. Emoji arrive as nothing (Zoom renders them as images).

New messages are found by **order**, not as a set: the same person can send
the same text twice in the same minute, and the time only has minute
precision. The window's list is the whole conversation in order, so the
previous snapshot's tail must reappear as a prefix of the new one (the list
may also lose its oldest rows); whatever follows the overlap is new. A
snapshot with nothing in common with the previous one is history, never a
batch of new rows.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^\d{1,2}:\d{2}(\s?[AaPp][Mm])?$")


@dataclass(frozen=True)
class Row:
    sender: str
    text: str
    time: str

    def key(self) -> tuple[str, str, str]:
        return (self.sender, self.text, self.time)

    def loose_key(self) -> tuple[str, str]:
        """Without the sender: a participant who renames themselves gets their past rows relabelled."""
        return (self.text, self.time)


def parse_row(name: str) -> Optional[Row]:
    """One list item's accessible name → a message row, or None for system rows."""
    if not name:
        return None
    parts = [p.strip() for p in name.split(", ")]
    while parts and not parts[-1]:
        parts.pop()
    # sender, "", text…, time
    if len(parts) < 4 or parts[1] != "" or not TIME_RE.match(parts[-1]):
        return None
    sender = parts[0]
    text = ", ".join(parts[2:-1]).strip()
    if not sender:
        return None
    return Row(sender=sender, text=text, time=parts[-1])


def rows_from_names(names: list[str]) -> list[Row]:
    return [r for r in (parse_row(n) for n in names) if r is not None]


def new_rows(prev: list[Row], cur: list[Row]) -> tuple[list[Row], str]:
    """The rows in ``cur`` that arrived after ``prev``, and how they were found.

    ``"append"`` — prev's tail is cur's head (the normal case, also when the
    oldest rows scrolled out of the tree, or when a renamed sender relabelled
    rows). ``"realign"`` — no clean overlap (e.g. a message was deleted
    mid-list): the last matching block decides. ``"unrelated"`` — nothing in
    common (another chat, or the window came back): all of it is history.
    """
    if not prev:
        return list(cur), "append"
    if not cur:
        return [], "append"
    # The exact keys first: without the sender, one person's repeated answer
    # could pass for a longer overlap and swallow a new row.
    for key in (Row.key, Row.loose_key):
        pk = [key(r) for r in prev]
        ck = [key(r) for r in cur]
        for overlap in range(min(len(pk), len(ck)), 0, -1):
            if pk[len(pk) - overlap:] == ck[:overlap]:
                return list(cur[overlap:]), "append"
    pk = [r.key() for r in prev]
    ck = [r.key() for r in cur]
    blocks = [b for b in difflib.SequenceMatcher(a=pk, b=ck, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        logger.warning("⚠️ chat: the new snapshot shares nothing with the previous one — taking its %d rows as history", len(ck))
        return [], "unrelated"
    last = blocks[-1]
    return list(cur[last.b + last.size:]), "realign"
