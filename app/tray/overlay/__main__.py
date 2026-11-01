"""The overlay process: ``python -m app.tray.overlay --server http://127.0.0.1:8449 [--parent-pid N]``.

A thin client of the live socket (``/ws?role=overlay``): it reads ``state.copilot`` from each
snapshot (the hints, Copilot on or off, the overlay's own settings, the time of Settings' last
"Show a sample") and draws nothing else; the server keeps every bit of state.
The server starts it with Copilot (or for a sample) and stops it when Copilot goes off. It exits by
itself when the server's process is gone, so a crashed server never leaves an overlay behind.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import queue
import ssl
import sys
import threading
import time
from typing import Any, Optional

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from src.config import load_config
from src.logger import configure_logging, log_path

logger = logging.getLogger(__name__)

RETRY_S = 2.0
SCREENS_EVERY_S = 10.0  # a monitor plugged in or out is picked up within this
FAST_TICK_MS, IDLE_TICK_MS = 33, 250


def socket_url(server: str) -> str:
    """``http(s)://host:port`` → the live socket's ``ws(s)://host:port/ws?role=overlay``."""
    base = server.rstrip("/")
    scheme, _, rest = base.partition("://")
    return f"{'wss' if scheme == 'https' else 'ws'}://{rest}/ws?role=overlay"


def listen(url: str, inbox: queue.Queue, stop: threading.Event) -> None:
    """Keep the socket up; hand every snapshot to the window loop."""
    context = ssl._create_unverified_context() if url.startswith("wss") else None  # noqa: S323 — loopback, our own server
    warned = False
    while not stop.is_set():
        try:
            with connect(url, ssl=context, open_timeout=5, close_timeout=1) as ws:
                logger.info("✅ overlay connected to %s", url)
                warned = False
                for raw in ws:
                    msg = json.loads(raw)
                    if isinstance(msg, dict) and msg.get("type") == "state":
                        inbox.put(msg)
        except (OSError, WebSocketException, ValueError) as exc:
            if not warned:
                logger.warning("⚠️ overlay: the server at %s is not answering (%s) — retrying", url, exc)
                warned = True
        inbox.put({"type": "offline"})
        stop.wait(RETRY_S)


class ParentWatch:
    """The server's process, held open from the start (a PID reused later can never pass for it)."""

    def __init__(self, pid: Optional[int]) -> None:
        self.handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid) if pid else None  # SYNCHRONIZE

    def gone(self) -> bool:
        if self.handle is None:
            return False
        return ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0) != 0x102  # anything but WAIT_TIMEOUT


def run(inbox: queue.Queue, parent: ParentWatch) -> int:
    from app.tray.overlay.behaviour import Behaviour
    from app.tray.overlay.render import frame, sample_hints
    from app.tray.overlay.screens import list_screens, pick, place
    from app.tray.overlay.window import OverlayWindow

    behaviour = Behaviour()
    screens, screens_at = list_screens(), time.monotonic()
    drawn: Any = None
    last_alpha = -1
    with OverlayWindow() as window:
        while not parent.gone():
            now = time.monotonic()
            while True:
                try:
                    msg = inbox.get_nowait()
                except queue.Empty:
                    break
                if msg["type"] == "state":
                    behaviour.on_state((msg.get("state") or {}).get("copilot") or {}, now,
                                       int(msg.get("server_now") or 0), sample_hints())
                else:
                    behaviour.fade_out(now)
            if now - screens_at > SCREENS_EVERY_S:
                screens, screens_at = list_screens(), now
            alpha = behaviour.alpha(now)
            popup = behaviour.popup
            if popup is None or alpha == 0:
                window.hide()
                drawn, last_alpha = None, -1
            elif drawn != (id(popup), behaviour.style_rev, len(screens)):
                style = behaviour.style
                screen = pick(screens, str(style.get("screen") or ""))
                img = frame(popup.hints, popup.mode, style, screen.scale)
                if img is not None:
                    x, y = place(screen, str(style.get("position") or ""), int(style.get("margin") or 0), *img.size)
                    window.draw(img, x, y, alpha)
                    logger.info("ℹ️ overlay: %d hint(s), %s mode%s, on %s at %d,%d (%dx%d)", len(popup.hints), popup.mode,
                                " (sample)" if popup.sample else "", screen.id or "the primary screen", x, y, *img.size)
                drawn, last_alpha = (id(popup), behaviour.style_rev, len(screens)), alpha
            elif alpha != last_alpha:
                window.set_alpha(alpha)
                last_alpha = alpha
            window.pump(FAST_TICK_MS if behaviour.animating() else IDLE_TICK_MS)
    logger.info("ℹ️ overlay: the server process is gone — exiting")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="The live copilot's overlay window.")
    ap.add_argument("--server", required=True, help="the server's base URL, e.g. http://127.0.0.1:8449")
    ap.add_argument("--parent-pid", type=int, help="exit when this process (the server) is gone")
    args = ap.parse_args(argv)
    configure_logging(log_file=log_path("overlay"))
    if sys.platform != "win32":
        logger.error("❌ the overlay needs Windows")
        return 2
    # Per-monitor DPI aware: positions and sizes are each monitor's real pixels.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    inbox: queue.Queue = queue.Queue()
    stop = threading.Event()
    url = socket_url(args.server)
    threading.Thread(target=listen, args=(url, inbox, stop), name="overlay-socket", daemon=True).start()
    logger.info("✅ overlay up — %s (config %s)", url, load_config().source)
    try:
        return run(inbox, ParentWatch(args.parent_pid))
    finally:
        stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
