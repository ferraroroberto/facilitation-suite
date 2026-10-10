"""Teams chat tree → messages (pure, unit-tested; #237).

Teams (on the web, and the new desktop app, both Chromium) exposes the open
chat to UI Automation as a tree keyed by automation ids. The message list is
the element with id ``chat-pane-list``; inside it, each message is

- a body group ``message-body-<N>`` whose class names the kind:
  ``fui-ChatMessage__body`` (someone else), ``fui-ChatMyMessage__body`` (the
  facilitator) or ``fui-ChatControlMessage`` (a system line: meeting
  started/ended, someone invited — dropped);
- a group ``author-<N>`` whose first named descendant is the sender;
- a group ``content-<N>`` whose name is the text (empty for cards, whose text
  is then their text nodes' names joined).

``N`` is the message's arrival time in epoch milliseconds, so the time comes
from the id (local ``HH:MM``, like Zoom's), never from a locale-formatted
string. The facilitator's own messages get the sender ``"You"`` — Zoom's
convention, which the hub and the quiz already treat as "not an answer".
Day dividers and the per-message headings (``"<text> by <sender>"``) are
ignored: the ids carry everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from src.chat.parse import Row

LIST_ID = "chat-pane-list"
OWN_SENDER = "You"
_ID_RE = re.compile(r"^(message-body|author|content)-(\d+)$")
# A message id is epoch ms; anything outside 2001–2286 is not one.
_MS_MIN, _MS_MAX = 10**12, 10**13


@dataclass(frozen=True)
class UNode:
    """One UI Automation element: tree depth, class name, automation id, name."""

    depth: int
    cls: str
    aid: str
    name: str


def time_from_id(msg_id: str) -> str:
    """``"1691152031576"`` → local ``"14:27"``; ``""`` if the id is not epoch ms."""
    try:
        ms = int(msg_id)
    except ValueError:
        return ""
    if not _MS_MIN <= ms < _MS_MAX:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _subtree(nodes: list[UNode], i: int) -> list[UNode]:
    """The descendants of ``nodes[i]`` (document order)."""
    out = []
    for n in nodes[i + 1:]:
        if n.depth <= nodes[i].depth:
            break
        out.append(n)
    return out


def rows_from_tree(nodes: list[UNode]) -> tuple[list[tuple[str, Row]], bool]:
    """The ``(message id, row)`` pairs in the chat list, in order, and whether
    the list was found.

    ``nodes`` is a document-order walk; only what sits under ``chat-pane-list``
    counts, so the chat list on the left (its previews) is never read.
    """
    start = next((i for i, n in enumerate(nodes) if n.aid == LIST_ID), None)
    if start is None:
        return [], False
    inside = _subtree(nodes, start)
    order: list[str] = []
    kind: dict[str, str] = {}
    sender: dict[str, str] = {}
    text: dict[str, str] = {}
    for i, n in enumerate(inside):
        m = _ID_RE.match(n.aid)
        if not m:
            continue
        part, mid = m.groups()
        if part == "message-body":
            if mid not in kind:
                order.append(mid)
            kind[mid] = "control" if "ChatControlMessage" in n.cls else ("own" if "ChatMyMessage" in n.cls else "other")
        elif part == "author":
            sender[mid] = next((d.name.strip() for d in _subtree(inside, i) if d.name.strip()), "")
        elif part == "content":
            # A card: its text nodes (no class name), not its buttons and toolbars.
            body = n.name.strip() or " ".join(d.name.strip() for d in _subtree(inside, i) if not d.cls and d.name.strip())
            text[mid] = body
    rows: list[tuple[str, Row]] = []
    for mid in order:
        if kind[mid] == "control":
            continue
        who = OWN_SENDER if kind[mid] == "own" else sender.get(mid, "")
        if not who:
            continue
        rows.append((mid, Row(sender=who, text=text.get(mid, ""), time=time_from_id(mid))))
    return rows, True


def candidates(titles: list[tuple[int, str]], marker: str) -> list[int]:
    """The windows whose title carries the marker (``"Microsoft Teams"``), an
    open chat (``"Chat | … | Microsoft Teams"``) first. A browser's title is
    its active tab's, so a Teams tab must be the active one in its window."""
    hits = [(h, t) for h, t in titles if marker and marker in t]
    return [h for h, t in hits if t.startswith("Chat |")] + [h for h, t in hits if not t.startswith("Chat |")]
