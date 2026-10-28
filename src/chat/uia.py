"""Read the open Teams or Google Meet chat through Windows UI Automation (#237).

Teams on the web and the new Teams desktop app are Chromium (WebView2), which
exposes the page to UI Automation — MSAA reaches the same text but without the
class names and automation ids the parser keys on (``src/chat/teams.py``).
Read only: the walk asks for properties and never uses a control pattern, so
nothing is clicked, focused, scrolled or typed.

Chromium exposes only the active tab, so a Teams or Meet tab must be the active
one in its browser window. Teams' message list is found by automation id; Meet's
chat panel by its name, after which the whole window is read (``src/chat/meet.py``
finds the panel in it). Either way it is fetched in one cached request (the whole
subtree in one cross-process round trip).

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
    cond = uia.CreatePropertyCondition(U.UIA_AutomationIdPropertyId, LIST_ID)
    found = root.FindFirstBuildCache(U.TreeScope_Descendants, cond, _subtree_cache(uia, U, with_type=False))
    return _flatten(found, max_nodes, with_type=False) if found else None


def meet_window(hwnd: int, panel: str, max_nodes: int = MAX_NODES) -> Optional[list[UNode]]:
    """The whole window in document order when it shows the Meet chat panel (an
    element named ``panel``), or None. Meet has no ids to aim at, so the parser
    (``src/chat/meet.py``) finds the panel inside the page."""
    uia, U = _client()
    root = uia.ElementFromHandle(hwnd)
    cond = uia.CreatePropertyCondition(U.UIA_NamePropertyId, panel)
    # The search goes through a raw-view cache request: the default control view hides the page.
    if not root.FindFirstBuildCache(U.TreeScope_Descendants, cond, _subtree_cache(uia, U, with_type=False)):
        return None
    return _flatten(root.BuildUpdatedCache(_subtree_cache(uia, U, with_type=True)), max_nodes, with_type=True)


def _subtree_cache(uia: Any, U: Any, *, with_type: bool) -> Any:
    """A request for the whole subtree's names, class names, ids (and control types) in one round trip."""
    cache = uia.CreateCacheRequest()
    props = [U.UIA_ClassNamePropertyId, U.UIA_AutomationIdPropertyId, U.UIA_NamePropertyId]
    for prop in props + ([U.UIA_ControlTypePropertyId] if with_type else []):
        cache.AddProperty(prop)
    cache.TreeScope = U.TreeScope_Subtree
    cache.TreeFilter = uia.RawViewCondition  # the default control view hides Chromium's page content
    return cache


def _flatten(found: Any, max_nodes: int, *, with_type: bool) -> list[UNode]:
    """The cached subtree of ``found`` in document order, ``found`` itself first at depth 0."""
    out: list[UNode] = []

    def visit(el: Any, depth: int) -> None:
        if len(out) >= max_nodes:
            return
        out.append(UNode(depth, el.CachedClassName or "", el.CachedAutomationId or "", el.CachedName or "",
                         el.CachedControlType if with_type else 0))
        kids = el.GetCachedChildren()
        if not kids:  # a leaf: a NULL pointer, not None
            return
        for i in range(kids.Length):
            visit(kids.GetElement(i), depth + 1)

    visit(found, 0)
    return out
