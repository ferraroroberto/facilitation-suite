"""The local listener: the main app again, in plain HTTP on ``127.0.0.1:<local_port>``.

The HTTPS port serves one certificate, which names only the tailnet host, so this
PC reaching ``https://127.0.0.1`` would meet a certificate warning on the presenter
and the stage. Browsers treat ``http://127.0.0.1`` as a secure context, so this
listener needs no certificate and no tailnet hop. Other devices keep the HTTPS URL.

Same shape as the quiz player listener (``app/player/listener.py``), whose socket
and server helpers it reuses:

- **Loopback only, always.** Whatever ``config.host`` says, it binds ``127.0.0.1``;
  the app's auth gate already treats a loopback caller as this PC.
- **Same app, same loop.** A second ``uvicorn.Server`` serves the very same ASGI app
  (lifespan off: the main server runs it once) as a task on the main event loop.
- **Optional.** A busy port logs one ❌ line and the main app keeps serving; the tray
  then falls back to the HTTPS URL.
- ``local_port = 0`` turns it off (the unit tests do).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

import uvicorn
from fastapi import FastAPI

from app.player.listener import HOST, START_TIMEOUT_S, STOP_TIMEOUT_S, EmbeddedServer, bind_player_socket

logger = logging.getLogger(__name__)


class LocalListener:
    """Owns the local uvicorn server for the lifetime of the main app."""

    def __init__(self, port: int, app: FastAPI) -> None:
        self.port = port
        self.app = app
        self.server: Optional[EmbeddedServer] = None
        self.task: Optional[asyncio.Task[None]] = None

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.started and self.task is not None and not self.task.done()

    async def start(self) -> bool:
        """Bind and serve; ``False`` (logged) when disabled, busy or not up in time — never raises."""
        if not self.port:
            logger.info("ℹ️ local listener off (local_port = 0)")
            return False
        try:
            sock = bind_player_socket(self.port)
        except (OSError, OverflowError) as exc:
            logger.error(
                "❌ local listener: %s:%d is busy or unusable (%s) — this PC falls back to the HTTPS URL; "
                "the main app keeps serving.", HOST, self.port, exc,
            )
            return False
        config = uvicorn.Config(
            self.app,
            lifespan="off",
            log_config=None,  # the process's logging is already configured; don't reset it
            access_log=True,  # False would empty the shared uvicorn.access logger for the main app too
            proxy_headers=False,  # nothing proxies in front of it: a forwarded header must not rewrite the client
            ws_ping_interval=20.0,
            ws_ping_timeout=20.0,
        )
        self.server = EmbeddedServer(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]), name="local-listener")
        self.task.add_done_callback(self._on_done)
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT_S
        while not self.server.started and not self.task.done():
            if asyncio.get_running_loop().time() > deadline:
                logger.error("❌ local listener: not serving %s:%d after %.0f s", HOST, self.port, START_TIMEOUT_S)
                await self.stop()
                return False
            await asyncio.sleep(0.02)
        if not self.running:
            return False
        logger.info("✅ local listener on http://%s:%d (this PC only)", HOST, self.port)
        return True

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("❌ local listener crashed: %r", exc, exc_info=exc)

    async def stop(self) -> None:
        """Ask the server to finish; force it after ``STOP_TIMEOUT_S``."""
        if self.server is None or self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=STOP_TIMEOUT_S)
        except TimeoutError:
            logger.warning("⚠️ local listener: no clean stop in %.0f s — forcing it", STOP_TIMEOUT_S)
            self.server.force_exit = True
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        except Exception:  # noqa: BLE001 — already logged by _on_done
            pass
        logger.info("👋 local listener stopped")
        self.server = None
        self.task = None
