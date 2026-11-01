"""The monitors this PC shows, and where on one of them the overlay sits.

Coordinates are physical pixels of the virtual desktop (the overlay process is per-monitor DPI
aware), and the overlay keeps to a monitor's work area, so it never lands under the taskbar. A
monitor is named by its device name (``\\\\.\\DISPLAY2``): the Settings card lists them, and a name
that is gone (a monitor unplugged) falls back to the primary.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass
from typing import Any

from src.config import OVERLAY_POSITIONS


@dataclass(frozen=True)
class Screen:
    id: str
    primary: bool
    x: int
    y: int
    width: int
    height: int
    scale: float  # the monitor's DPI over 96

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def _dpi_scale(monitor: Any) -> float:
    dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
    try:
        ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    except (AttributeError, OSError):
        return 1.0
    return round(dpi_x.value / 96, 2) or 1.0


def list_screens() -> list[Screen]:
    """Every monitor's work area, primary first (empty off Windows or when none can be read)."""
    if sys.platform != "win32":
        return []
    found: list[Screen] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def visit(monitor: Any, _dc: Any, _rect: Any, _data: Any) -> bool:
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            found.append(Screen(info.szDevice, bool(info.dwFlags & 1), work.left, work.top,
                                work.right - work.left, work.bottom - work.top, _dpi_scale(monitor)))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_type(visit), 0)
    return sorted(found, key=lambda s: (not s.primary, s.x, s.y))


def pick(screens: list[Screen], screen_id: str) -> Screen:
    """The monitor named ``screen_id``, else the primary (a 1920×1080 stand-in when none is known)."""
    for s in screens:
        if s.id == screen_id:
            return s
    return next((s for s in screens if s.primary), screens[0] if screens else Screen("", True, 0, 0, 1920, 1080, 1.0))


def place(screen: Screen, position: str, margin: int, width: int, height: int) -> tuple[int, int]:
    """Top-left corner of a ``width`` × ``height`` window at ``position`` on ``screen``'s work area."""
    position = position if position in OVERLAY_POSITIONS else "top-center"
    m = round(max(0, margin) * screen.scale)
    vertical, _, horizontal = position.partition("-")
    if position == "center":
        vertical, horizontal = "middle", "center"
    x = {"left": screen.x + m, "right": screen.x + screen.width - width - m}.get(horizontal, screen.x + (screen.width - width) // 2)
    y = {"top": screen.y + m, "bottom": screen.y + screen.height - height - m}.get(vertical, screen.y + (screen.height - height) // 2)
    return x, y
