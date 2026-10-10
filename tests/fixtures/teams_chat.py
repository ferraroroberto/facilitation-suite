"""A synthetic Teams chat tree in the shape the UI Automation probe recorded (#237).

Structure only: the class names, automation ids and nesting are Teams' (on the
web, 2026-10); every name, text and id here is made up.
"""

from __future__ import annotations

from src.chat.teams import UNode

# A message: (kind, epoch-ms id, sender, text); kind is "other", "own" or "control".
Message = tuple[str, int, str, str]


def _message(depth: int, kind: str, mid: int, sender: str, text: str) -> list[UNode]:
    d = depth
    if kind == "control":
        return [
            UNode(d, "fui-Primitive ___unfo430", f"control-message-{mid}", text),
            UNode(d + 1, "", "", text),
            UNode(d, "fui-ChatControlMessage ___vikt0e0", f"message-body-{mid}", text),
            UNode(d + 1, "fui-Primitive ___qd2wld0", f"content-control-message-{mid}", ""),
            UNode(d + 2, "", "", text),
        ]
    own = kind == "own"
    body_cls = "fui-ChatMyMessage__body rojuldx" if own else "fui-ChatMessage__body rojuldx"
    author_cls = "fui-ChatMyMessage__author ___unfo430" if own else "fui-ChatMessage__author ___s3z1fc0"
    return [
        UNode(d, "fui-Primitive ___unfo430", "", f"{text} by {sender}"),
        UNode(d + 1, "", "", f"{text} by {sender}"),
        UNode(d, "fui-Primitive", "menur1", ""),
        UNode(d + 1, author_cls, "", ""),
        UNode(d + 2, "fui-StyledText ___s8pl3v0", f"author-{mid}", ""),
        UNode(d + 3, "", "", sender),
        UNode(d + 1, "fui-ChatMessage__avatar ___r7vjx20", "", ""),
        UNode(d + 1, body_cls, f"message-body-{mid}", f"{sender} {text} Friday, 4 September 2026 6:41 PM."),
        UNode(d + 2, "fui-Primitive rrd10u0", "", "More message options"),
        UNode(d + 2, "fui-Primitive ___11tzqds", f"content-{mid}", text),
        UNode(d + 3, "", "", ""),
        UNode(d + 4, "", "", text),
    ]


def card(depth: int, mid: int, sender: str, lines: list[str]) -> list[UNode]:
    """A card message (a meeting invite): the content group has no name of its own."""
    return [
        UNode(depth, "fui-Primitive", "menur2", ""),
        UNode(depth + 1, "fui-ChatMessage__author ___s3z1fc0", "", ""),
        UNode(depth + 2, "fui-StyledText ___s8pl3v0", f"author-{mid}", ""),
        UNode(depth + 3, "", "", sender),
        UNode(depth + 1, "fui-ChatMessage__body rojuldx", f"message-body-{mid}", f"{sender} {' '.join(lines)}"),
        UNode(depth + 2, "fui-Primitive rrd10u0", "", "More message options"),
        UNode(depth + 2, "fui-Primitive ___11tzqds", f"content-{mid}", ""),
        *[UNode(depth + 3, "", "", line) for line in lines],
        UNode(depth + 3, "fui-Toolbar ___56tqxz0", "", "Meeting actions"),
        UNode(depth + 4, "fui-Button r1f29ykk", "", "Add to calendar"),
    ]


def teams_tree(messages: list[Message], *, with_sidebar: bool = True, extra: list[UNode] | None = None) -> list[UNode]:
    """The window as the reader walks it: the chat list on the left (whose
    previews must never be read), then ``chat-pane-list`` with a day divider
    and the messages."""
    nodes: list[UNode] = [UNode(0, "", "RootWebArea", "Chat | Weekly workshop | Microsoft Teams")]
    if with_sidebar:
        nodes += [
            UNode(1, "fui-Tree rnv2ez3", "", "Teams"),
            UNode(2, "fui-TreeItem r15xhw3a", "menur15", "Meeting chat Weekly workshop Last message Kim Ito: a preview, not a message"),
            UNode(3, "___1suw0t6", "message-preview-chat-list-item_19:meeting_AAAA", ""),
        ]
    nodes += [
        UNode(1, "fui-Flex vdi-occlusion", "message-pane-layout-a11y", ""),
        UNode(2, "fui-Flex ___xuy9gc0", "", "Message List"),
        UNode(3, "fui-Chat ___1vlkg3r", "chat-pane-list", ""),
        UNode(4, "fui-Divider ___1x0sra0", "", "Friday, September 4, 2026 6:30 PM"),
        UNode(5, "", "", "Friday, September 4, 2026 6:30 PM"),
    ]
    for kind, mid, sender, text in messages:
        nodes += _message(4, kind, mid, sender, text)
    nodes += extra or []
    nodes += [UNode(1, "ck-blurred fui-Primitive", "new-message-0000", "Type a message")]
    return nodes


def as_list(nodes: list[UNode]) -> list[UNode]:
    """The ``chat-pane-list`` subtree re-based at depth 0, as ``uia.chat_list`` returns it."""
    start = next(i for i, n in enumerate(nodes) if n.aid == "chat-pane-list")
    base = nodes[start].depth
    out = [nodes[start]]
    for n in nodes[start + 1:]:
        if n.depth <= base:
            break
        out.append(UNode(n.depth - base, n.cls, n.aid, n.name))
    return [UNode(0, out[0].cls, out[0].aid, out[0].name), *out[1:]]
