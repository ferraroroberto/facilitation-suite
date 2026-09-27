"""A stand-in for Zoom's popped-out chat: a real Win32 window with a LISTBOX.

A standard LISTBOX exposes its rows through MSAA as list items (role 34),
the same shape Zoom's chat has, so the reader's real code path — find the
window, MSAA walk, parse, diff — runs against it without Zoom. Rows use
Zoom's accessible-name format ``"<Sender>, , <text>, <HH:MM>, "``.

Windows only. Like Zoom, the window lives in **another process** (MSAA
across processes, as in real life; an in-process walk also trips pytest's
faulthandler on handled proxy faults): ``FakeZoomChat()`` spawns
``python -m tests.fixtures.fake_zoom_chat`` and drives it over stdin, one
JSON command per line. The window sits off-screen and closes with ``close()``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import threading
from typing import Optional

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE, WS_VSCROLL = 0x00CF0000, 0x40000000, 0x10000000, 0x00200000
LBS_NOINTEGRALHEIGHT = 0x0100
WS_EX_TOOLWINDOW = 0x00000080
LB_ADDSTRING, LB_DELETESTRING, LB_GETCOUNT = 0x0180, 0x0182, 0x018B
WM_CLOSE, WM_DESTROY, WM_USER = 0x0010, 0x0002, 0x0400
WM_APP_ADD = WM_USER + 1

user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


def row(sender: str, text: str, time: str = "18:41") -> str:
    return f"{sender}, , {text}, {time}, "


class _Window:
    """The window itself (runs inside the helper process)."""

    def __init__(self, window_class: str = "FSFakeZoomChatWnd", title: str = "Meeting chat (test)") -> None:
        self.window_class, self.title = window_class, title
        self.hwnd: Optional[int] = None
        self.hlist: Optional[int] = None
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)  # keep a reference: the callback must outlive the window
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(10) or not self.hwnd:
            raise RuntimeError("the fake chat window did not open")

    def _wndproc(self, hwnd: int, msg: int, wp: int, lp: int) -> int:
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=hinst,
                         lpszClassName=self.window_class)
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(WS_EX_TOOLWINDOW, self.window_class, self.title, WS_OVERLAPPEDWINDOW,
                                           -3000, -3000, 400, 600, None, None, hinst, None)
        # MSAA names a list box after the static label just before it: name it as Zoom names its history list.
        user32.CreateWindowExW(0, "STATIC", "Chat messages history , Ctrl+Shift+U", WS_CHILD | WS_VISIBLE,
                               0, 0, 380, 20, self.hwnd, None, hinst, None)
        self.hlist = user32.CreateWindowExW(0, "LISTBOX", "",
                                            WS_CHILD | WS_VISIBLE | WS_VSCROLL | LBS_NOINTEGRALHEIGHT,
                                            0, 24, 380, 540, self.hwnd, None, hinst, None)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterClassW(self.window_class, hinst)

    def add(self, *rows: str) -> None:
        for r in rows:
            buf = ctypes.create_unicode_buffer(r)
            user32.SendMessageW(self.hlist, LB_ADDSTRING, 0, ctypes.cast(buf, ctypes.c_void_p).value)

    def delete(self, index: int) -> None:
        user32.SendMessageW(self.hlist, LB_DELETESTRING, index, 0)

    def count(self) -> int:
        return int(user32.SendMessageW(self.hlist, LB_GETCOUNT, 0, 0))

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(5)
            self.hwnd = None


class FakeZoomChat:
    """Test-side handle: the window runs in a child process."""

    def __init__(self, window_class: str = "FSFakeZoomChatWnd", title: str = "Meeting chat (test)") -> None:
        self.window_class, self.title = window_class, title
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.fixtures.fake_zoom_chat", window_class, title],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ready = self.proc.stdout.readline().strip()
        if not ready.startswith("ready"):
            raise RuntimeError(f"the fake chat window did not open: {ready!r}")
        self.hwnd = int(ready.split()[1])

    def _cmd(self, cmd: dict) -> str:
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()
        return self.proc.stdout.readline().strip()

    def add(self, *rows: str) -> None:
        self._cmd({"add": list(rows)})

    def delete(self, index: int) -> None:
        self._cmd({"delete": index})

    def count(self) -> int:
        return int(self._cmd({"count": True}))

    def close(self) -> None:
        if self.proc.poll() is None:
            self._cmd({"quit": True})
            self.proc.wait(10)


def _serve(window_class: str, title: str) -> int:
    win = _Window(window_class, title)
    print(f"ready {win.hwnd}", flush=True)
    for line in sys.stdin:
        cmd = json.loads(line)
        if "add" in cmd:
            win.add(*cmd["add"])
        elif "delete" in cmd:
            win.delete(int(cmd["delete"]))
        elif "count" in cmd:
            print(win.count(), flush=True)
            continue
        elif "quit" in cmd:
            win.close()
            print("bye", flush=True)
            return 0
        print("ok", flush=True)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_serve(sys.argv[1], sys.argv[2]))
