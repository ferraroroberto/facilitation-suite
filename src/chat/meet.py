"""Google Meet chat tree → messages (pure, unit-tested).

Meet runs in Chrome or Edge, which expose the active tab to UI Automation. The
in-call chat is a side panel named ``"In-call messages"`` (``reader.meet_panel``)
and Meet's class names are generated and change between releases, so the parser
keys on structure, never on a class name:

- a **header** is an element whose text is exactly a sender and a time
  (``"Alex Rivera"``, ``"10:42 AM"``) — or the first two children of a group
  are those two texts;
- the header's parent is a **group**: one sender's consecutive messages. Its
  other children are the messages (when they sit in one wrapper, that wrapper's
  children are), each the text nodes below it joined;
- text outside every group — the panel's title, its notice that messages are
  not kept, system lines, the message box — is not a message.

Only text nodes count (``UIA_TextControlTypeId``), so buttons such as *Pin
message* and the *Send a message* box never reach a row. The panel is the
smallest element around the ``"In-call messages"`` title that holds a group, so
captions and participant tiles elsewhere on the page are never read. The
facilitator's own messages arrive under the sender ``"You"`` — Zoom's
convention, which the hub and the quiz already treat as "not an answer".
"""

from __future__ import annotations

import re
from typing import Optional

from src.chat.parse import Row
from src.chat.teams import UNode

TEXT = 50020  # UIA_TextControlTypeId: Chromium's static text
_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})(?:\s*([ap])\.?\s*m\.?)?$", re.IGNORECASE)


def to_hhmm(label: str) -> str:
    """``"2:05 PM"`` → ``"14:05"``, ``"14:05"`` → ``"14:05"``; ``""`` if it is not a time."""
    m = _TIME_RE.match(label.strip())
    if not m:
        return ""
    h, mins, half = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if half:
        if not 1 <= h <= 12:
            return ""
        h = h % 12 + (12 if half == "p" else 0)
    return f"{h:02d}:{mins:02d}" if h < 24 and mins < 60 else ""


class _Tree:
    """The walk with each element's span, parent and children, and which elements are
    one sender's group, worked out once, bottom-up (children come after their parent)."""

    def __init__(self, nodes: list[UNode]) -> None:
        self.nodes = nodes
        n = len(nodes)
        self.end = [n] * n
        self.parent: list[Optional[int]] = [None] * n
        self.kids: list[list[int]] = [[] for _ in range(n)]
        stack: list[int] = []
        for i, node in enumerate(nodes):
            while stack and nodes[stack[-1]].depth >= node.depth:
                self.end[stack.pop()] = i
            if stack:
                self.parent[i] = stack[-1]
                self.kids[stack[-1]].append(i)
            stack.append(i)
        self.texts = [0] * n  # text nodes in each subtree
        self.groups: dict[int, tuple[str, str, list[int]]] = {}
        self.holds = [False] * n  # the subtree holds a group
        for i in range(n - 1, -1, -1):
            self.texts[i] = int(self._text(i) != "") + sum(self.texts[k] for k in self.kids[i])
            g = self._group(i)
            if g is not None:
                self.groups[i] = g
            self.holds[i] = g is not None or any(self.holds[k] for k in self.kids[i])

    def _text(self, i: int) -> str:
        return self.nodes[i].name.strip() if self.nodes[i].ctype == TEXT else ""

    def _leaf_text(self, i: int) -> str:
        return self._text(i) if not self.kids[i] else ""

    def subtree_texts(self, i: int) -> list[str]:
        return [t for t in (self._text(k) for k in range(i, self.end[i])) if t]

    def _group(self, i: int) -> Optional[tuple[str, str, list[int]]]:
        """``(sender, HH:MM, message element indexes)`` when element ``i`` is one sender's group."""
        kids = self.kids[i]
        if not kids:
            return None
        first, second = self._leaf_text(kids[0]), self._leaf_text(kids[1]) if len(kids) > 1 else ""
        if first and second:
            sender, when, body = first, to_hhmm(second), kids[2:]
        elif not first and self.texts[kids[0]] == 2 and not self.holds[kids[0]]:
            # (A group whose one message reads as nothing also has two texts: holding a group, it is no header.)
            sender, when = self.subtree_texts(kids[0])
            when, body = to_hhmm(when), kids[1:]
        else:
            return None
        if not body or not when or to_hhmm(sender):
            return None
        if len(body) == 1 and self.kids[body[0]]:
            body = self.kids[body[0]]  # the messages share one wrapper
        if any(self.holds[k] for k in body):
            return None  # a message never holds a group: a system line with a time heads a list, not a group
        return sender, when, body

    def panel(self, title: int) -> tuple[int, int]:
        """The span of the smallest element around the panel title that holds a group (the title's own if none)."""
        i: Optional[int] = title
        while i is not None and not self.holds[i]:
            i = self.parent[i]
        return (title, self.end[title]) if i is None else (i, self.end[i])


def rows_from_tree(nodes: list[UNode], panel: str) -> tuple[list[Row], bool]:
    """The messages in the Meet chat panel, in order, and whether the panel was found.

    ``nodes`` is a document-order walk of the browser window; ``panel`` is the
    chat panel's title (``"In-call messages"``)."""
    title = next((i for i, n in enumerate(nodes) if panel and n.name.strip() == panel), None)
    if title is None:
        return [], False
    tree = _Tree(nodes)
    start, end = tree.panel(title)
    rows: list[Row] = []
    i = start
    while i < end:
        g = tree.groups.get(i)
        if g is None:
            i += 1
            continue
        sender, when, body = g
        for k in body:
            text = " ".join(tree.subtree_texts(k))
            if text:
                rows.append(Row(sender=sender, text=text, time=when))
        i = tree.end[i]
    return rows, True



def candidates(titles: list[tuple[int, str]], marker: str) -> list[int]:
    """The windows whose title names Meet as a word (``"Meeting chat"`` is Zoom's), a Meet tab's own
    ``"Meet - …"`` first. A browser's title is its active tab's, so the Meet tab must be the active one."""
    if not marker:
        return []
    word = re.compile(rf"\b{re.escape(marker)}\b")
    own = re.compile(rf"^{re.escape(marker)}\s*[-–—]")
    hits = [(h, t) for h, t in titles if word.search(t)]
    return [h for h, t in hits if own.match(t)] + [h for h, t in hits if not own.match(t)]
