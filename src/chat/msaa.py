"""Read the popped-out Zoom chat through Windows MSAA (epic §7.1).

UI Automation sees nothing inside Zoom's chat; MSAA does. The popped-out
meeting chat is a top-level window (class ``ZConfChatPopupContainerWndClass``,
title ``Meeting chat``). ``AccessibleObjectFromWindow(OBJID_CLIENT)`` then a
recursive ``AccessibleChildren`` walk yields ~500 nodes; each message is a
list item (role 34) whose name is ``"<Sender>, , <text>, <HH:MM>, "``.

Windows only; imported lazily by the reader so the rest of the app (and the
test suite) never loads COM.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

OBJID_CLIENT = -4
CHILDID_SELF = 0
ROLE_LIST = 33
ROLE_LISTITEM = 34
VT_I4 = 3
VT_DISPATCH = 9
SPI_GETSCREENREADER = 0x46
SPI_SETSCREENREADER = 0x47
SPIF_SENDCHANGE = 0x02
MAX_NODES = 20000  # a runaway tree must not freeze the reader

user32 = ctypes.windll.user32


@dataclass(frozen=True)
class Node:
    role: int
    name: str
    depth: int


def _com() -> tuple[Any, Any, Any]:
    """comtypes, the generated ``IAccessible`` and ``oleacc`` (loaded on first use)."""
    import comtypes
    import comtypes.client
    from comtypes.automation import VARIANT

    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen.Accessibility import IAccessible

    oleacc = ctypes.oledll.oleacc
    oleacc.AccessibleObjectFromWindow.argtypes = [wt.HWND, wt.DWORD, ctypes.POINTER(comtypes.GUID), ctypes.POINTER(ctypes.c_void_p)]
    oleacc.AccessibleChildren.argtypes = [ctypes.POINTER(IAccessible), ctypes.c_long, ctypes.c_long,
                                          ctypes.POINTER(VARIANT), ctypes.POINTER(ctypes.c_long)]
    return comtypes, IAccessible, (oleacc, VARIANT)


def find_window(window_class: str, title: str) -> Optional[int]:
    """The first top-level window with that class and title (visible or not)."""
    hwnd = user32.FindWindowW(window_class, title)
    return int(hwnd) if hwnd else None


def accessible_from_window(hwnd: int) -> Any:
    comtypes, IAccessible, (oleacc, _) = _com()
    ptr = ctypes.c_void_p()
    oleacc.AccessibleObjectFromWindow(hwnd, OBJID_CLIENT & 0xFFFFFFFF, ctypes.byref(IAccessible._iid_), ctypes.byref(ptr))
    return ctypes.cast(ptr, ctypes.POINTER(IAccessible))


def _prop(acc: Any, name: str, child: int = CHILDID_SELF) -> Any:
    try:
        return getattr(acc, name)(child)
    except Exception:  # noqa: BLE001 — COM raises on elements without the property
        return None


def walk(acc: Any, max_nodes: int = MAX_NODES) -> list[Node]:
    """Every node under ``acc`` in document order (role + name)."""
    _, IAccessible, (oleacc, VARIANT) = _com()
    out: list[Node] = []

    def visit(parent: Any, depth: int) -> None:
        if len(out) >= max_nodes:
            return
        try:
            count = int(parent.accChildCount)
        except Exception:  # noqa: BLE001
            return
        if count <= 0:
            return
        arr = (VARIANT * count)()
        got = ctypes.c_long()
        try:
            oleacc.AccessibleChildren(parent, 0, count, arr, ctypes.byref(got))
        except OSError:
            return
        for v in arr[: got.value]:
            if len(out) >= max_nodes:
                return
            if v.vt == VT_DISPATCH and v.value is not None:
                try:
                    child = v.value.QueryInterface(IAccessible)
                except Exception:  # noqa: BLE001
                    continue
                role = _prop(child, "accRole")
                out.append(Node(int(role) if isinstance(role, int) else 0, _prop(child, "accName") or "", depth))
                visit(child, depth + 1)
            elif v.vt == VT_I4:
                role = _prop(parent, "accRole", v.value)
                out.append(Node(int(role) if isinstance(role, int) else 0, _prop(parent, "accName", v.value) or "", depth))

    visit(acc, 0)
    return out


def screen_reader_flag() -> bool:
    val = wt.BOOL()
    user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(val), 0)
    return bool(val.value)


def set_screen_reader_flag(on: bool) -> None:
    """The "screen reader present" flag (§7.1: unproven whether Zoom needs it at
    window creation, so the reader sets it while it runs and restores it)."""
    user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1 if on else 0, None, SPIF_SENDCHANGE)
