"""The overlay's native window: a layered, always-on-top, click-through popup (Win32 through ctypes).

``WS_EX_LAYERED`` with per-pixel alpha (``UpdateLayeredWindow``) gives the card soft edges over any
app. ``WS_EX_TRANSPARENT`` passes every click to the window underneath, so the overlay never takes
a click meant for the call; ``WS_EX_TOOLWINDOW`` keeps it off the taskbar and Alt+Tab, and
``WS_EX_NOACTIVATE`` means it never takes the focus. A fade changes only the window's overall alpha.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from PIL import Image

from app.tray.overlay.render import premultiplied_bgra

EX_STYLE = 0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000  # LAYERED TRANSPARENT TOPMOST TOOLWINDOW NOACTIVATE
WS_POPUP = 0x80000000
ULW_ALPHA = 0x2
AC_SRC_ALPHA = 0x1
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE NOMOVE NOACTIVATE
QS_ALLINPUT = 0x04FF
PM_REMOVE = 0x1
CLASS_NAME = "facilitation-suite-overlay"

user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WndClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class _Blend(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _signatures() -> None:
    """Handles are pointer-sized: without these, a 64-bit HWND or HBITMAP would be cut to 32 bits."""
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                           ctypes.c_void_p, wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class OverlayWindow:
    """One layered popup, hidden until ``draw`` puts an image on screen."""

    def __init__(self) -> None:
        _signatures()
        self._proc = WNDPROC(lambda h, m, w, lp: user32.DefWindowProcW(h, m, w, lp))  # kept: Windows calls it
        hinst = kernel32.GetModuleHandleW(None)
        wc = _WndClass(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=CLASS_NAME)
        user32.RegisterClassW(ctypes.byref(wc))  # 0 when already registered in this process: fine
        self.hwnd = user32.CreateWindowExW(EX_STYLE, CLASS_NAME, "facilitation-suite overlay", WS_POPUP,
                                           0, 0, 1, 1, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"the overlay window could not be created (error {ctypes.GetLastError()})")
        self.visible = False

    def draw(self, img: Image.Image, x: int, y: int, alpha: int) -> None:
        width, height = img.size
        data = premultiplied_bgra(img)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        header = _BitmapInfoHeader(biSize=ctypes.sizeof(_BitmapInfoHeader), biWidth=width, biHeight=-height,
                                   biPlanes=1, biBitCount=32)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, data, len(data))
            old = gdi32.SelectObject(mem, bitmap)
            blend = _Blend(0, 0, max(0, min(255, alpha)), AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                       ctypes.byref(wintypes.SIZE(width, height)), mem,
                                       ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(mem, old)
        finally:
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
        if not self.visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.visible = True
        # Above any window that made itself topmost since (a call app's own always-on-top view).
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    def set_alpha(self, alpha: int) -> None:
        blend = _Blend(0, 0, max(0, min(255, alpha)), AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, None, None, None, None, None, 0, ctypes.byref(blend), ULW_ALPHA)

    def hide(self) -> None:
        if self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False

    def pump(self, timeout_ms: int) -> None:
        """Wait up to ``timeout_ms`` for window messages and dispatch them."""
        user32.MsgWaitForMultipleObjects(0, None, False, max(0, timeout_ms), QS_ALLINPUT)
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def close(self) -> None:
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def __enter__(self) -> OverlayWindow:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
