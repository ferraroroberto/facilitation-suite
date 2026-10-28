"""A synthetic Google Meet window with the in-call chat panel open.

A model, not a recording: no live Meet call was available to capture one. It has
the parts the parser must cope with — sender headers grouping consecutive
messages, times as Meet shows them, system lines and notices outside any group,
buttons and the message box, and participant tiles and captions elsewhere on the
page. Class names are left out on purpose: Meet's are generated, and the parser
never reads them. Every name and text here is made up.
"""

from __future__ import annotations

from src.chat.meet import TEXT
from src.chat.teams import UNode

GROUP, BUTTON, EDIT, WINDOW, DOCUMENT = 50026, 50000, 50004, 50032, 50030
PANEL = "In-call messages"

# One sender's run of messages: (sender, time as Meet shows it, texts). A text of
# "" is a message that reads as nothing (an emoji); a list is one message split
# over several text nodes (a link in it).
Group = tuple[str, str, list]


def _t(depth: int, name: str) -> UNode:
    return UNode(depth, "", "", name, TEXT)


def _message(depth: int, text: str | list[str]) -> list[UNode]:
    nodes = [UNode(depth, "", "", "", GROUP), UNode(depth + 1, "", "", "", GROUP)]
    for part in [text] if isinstance(text, str) else text:
        nodes.append(_t(depth + 2, part) if part else UNode(depth + 2, "", "", "", GROUP))
    nodes.append(UNode(depth + 1, "", "", "Pin message", BUTTON))
    return nodes


def _group(depth: int, sender: str, when: str, texts: list, layout: str) -> list[UNode]:
    d = depth
    if layout == "flat":  # sender and time straight under the group, then the messages
        return [UNode(d, "", "", "", GROUP), _t(d + 1, sender), _t(d + 1, when),
                *[n for text in texts for n in _message(d + 1, text)]]
    return [UNode(d, "", "", "", GROUP),
            UNode(d + 1, "", "", "", GROUP), _t(d + 2, sender), _t(d + 2, when),
            UNode(d + 1, "", "", "", GROUP), *[n for text in texts for n in _message(d + 2, text)]]


def meet_tree(groups: list[Group], *, layout: str = "wrapped", system: dict[int, str] | None = None,
              panel: bool = True, title: str = "Meet - abc-defg-hij - Google Chrome") -> list[UNode]:
    """The browser window as the reader walks it. ``system`` puts a system line
    before the group at that index (``len(groups)`` for one after them all)."""
    system = system or {}
    nodes = [UNode(0, "", "", title, WINDOW), UNode(1, "", "", "Meet - abc-defg-hij", DOCUMENT),
             # participant tiles and live captions: a name and a line, never a message
             UNode(2, "", "", "Alex Rivera", GROUP), _t(3, "Alex Rivera"),
             UNode(2, "", "", "Captions", GROUP), UNode(3, "", "", "", GROUP), _t(4, "Sam Taylor"), _t(4, "so the next step is")]
    if not panel:
        return nodes + [UNode(2, "", "", "Leave call", BUTTON)]
    nodes += [UNode(2, "", "", "", GROUP),  # the side panel
              UNode(3, "", "", "", GROUP), _t(4, PANEL), UNode(4, "", "", "Close", BUTTON),
              UNode(3, "", "", "Let everyone send messages", BUTTON),
              _t(3, "Unless they're pinned, messages won't be saved when the call ends."),
              UNode(3, "", "", "", GROUP)]  # the message list
    for i, (sender, when, texts) in enumerate(groups):
        if i in system:
            nodes += [UNode(4, "", "", "", GROUP), _t(5, system[i]), _t(5, "10:41 AM")]
        nodes += _group(4, sender, when, texts, layout)
    if len(groups) in system:
        nodes += [UNode(4, "", "", "", GROUP), _t(5, system[len(groups)])]
    nodes += [UNode(3, "", "", "Send a message", EDIT), UNode(3, "", "", "Send a message", BUTTON),
              UNode(2, "", "", "Leave call", BUTTON)]
    return nodes
