"""Read the open Teams chat through Windows UI Automation (#237).

Teams on the web and the new Teams desktop app are Chromium (WebView2), which
exposes the page to UI Automation — MSAA reaches the same text but without the
class names and automation ids the parser keys on (``src/chat/teams.py``).
Read only: the walk asks for properties and never uses a control pattern, so
nothing is clicked, focused, scrolled or typed.

Chromium exposes only the active tab, so a Teams tab must be the active one in
its browser window. The message list is found by automation id and fetched in
one cached request (the whole subtree in one cross-process round trip).

Windows only; imported lazily by the reader so the rest of the app (and the
test suite) never loads COM.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Any, Optional

from src.chat.teams import LIST_ID, UNode

logger = logging.getLogger(__name__)

MAX_NODES = 20000  # a runaway tree must not freeze the reader

user32 = ctypes.windll.user32
_uia: Optional[tuple[Any, Any]] = None


def _client() -> tuple[Any, Any]:
    """The ``IUIAutomation`` client and the generated module (created on first use)."""
    global _uia
    if _uia is None:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        _uia = (comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation), U)
    return _uia


def visible_titles() -> list[tuple[int, str]]:
    """Every visible top-level window with a title, in z-order."""
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            if user32.GetWindowTextW(hwnd, buf, 512):
                out.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(each, 0)
    return out


def chat_list(hwnd: int, max_nodes: int = MAX_NODES) -> Optional[list[UNode]]:
    """The ``chat-pane-list`` subtree of that window in document order (the list
    itself first, at depth 0), or None when the window shows no chat list."""
    uia, U = _client()
    root = uia.ElementFromHandle(hwnd)
    cache = uia.CreateCacheRequest()
    for prop in (U.UIA_ClassNamePropertyId, U.UIA_AutomationIdPropertyId, U.UIA_NamePropertyId):
        cache.AddProperty(prop)
    cache.TreeScope = U.TreeScope_Subtree
    cache.TreeFilter = uia.RawViewCondition  # the default control view hides Chromium's page content
    cond = uia.CreatePropertyCondition(U.UIA_AutomationIdPropertyId, LIST_ID)
    found = root.FindFirstBuildCache(U.TreeScope_Descendants, cond, cache)
    if not found:
        return None
    out: list[UNode] = []

    def visit(el: Any, depth: int) -> None:
        if len(out) >= max_nodes:
            return
        out.append(UNode(depth, el.CachedClassName or "", el.CachedAutomationId or "", el.CachedName or ""))
        kids = el.GetCachedChildren()
        if not kids:  # a leaf: a NULL pointer, not None
            return
        for i in range(kids.Length):
            visit(kids.GetElement(i), depth + 1)

    visit(found, 0)
    return out
