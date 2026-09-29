"""The second listener: the player app on ``127.0.0.1:<quiz.public_port>`` (#49).

Started inside the main app's lifespan as a task on the **same event loop**,
with the main app's one ``QuizService`` (the player routes call it directly —
no second engine), stopped when the main app stops. ``launcher.py`` and the
tray stay as they are.

- **Loopback only, always.** Whatever ``config.host`` says, this binds
  ``127.0.0.1``: the only way in from outside is the local ``cloudflared``
  (the Cloudflare tunnel, ``src/tunnel.py``) or, as the fallback, Tailscale Funnel.
- **Optional.** A busy port logs one distinct ❌ line and the main app keeps
  serving — the deck must never die because of the quiz. We bind the socket
  ourselves (uvicorn's own bind failure calls ``sys.exit``) with
  ``SO_EXCLUSIVEADDRUSE`` on Windows, after checking that no other process
  holds the port on the wildcard address either (Windows would otherwise let a
  loopback bind sit silently beside someone else's ``0.0.0.0`` listener).
- **Not the signal owner.** The main uvicorn server handles Ctrl+C / SIGTERM;
  this one never installs signal handlers.
- **Out of the access log.** Its request lines are dropped (``QuietPlayerRequests``
  on ``uvicorn.access``): 60 phones polling once a second would flood the log, and
  the polling URL carries each player's secret. The logger is shared with the main
  server, so this filters by path instead of uvicorn's ``access_log=False``, which
  would empty that logger's handlers for the main app too.
- ``public_port = 0`` turns it off (the unit tests do; e2e uses a free port).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Generator
from typing import Optional

import uvicorn
from fastapi import FastAPI

from app.player.app import PREFIX, create_player_app
from src.quiz.service import QuizService

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
START_TIMEOUT_S = 5.0
STOP_TIMEOUT_S = 5.0
_EXCLUSIVE = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)  # Windows only
ACCESS_LOGGER = "uvicorn.access"


class QuietPlayerRequests(logging.Filter):
    """Drop access-log lines for ``/play…`` (uvicorn's args: client, method, path, version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) > 2 and str(args[2]).startswith(PREFIX))


def quiet_player_requests() -> None:
    lg = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, QuietPlayerRequests) for f in lg.filters):
        lg.addFilter(QuietPlayerRequests())


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that leaves signal handling to the main server."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


def _exclusive_socket(family: socket.AddressFamily = socket.AF_INET) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    if _EXCLUSIVE is not None:
        sock.setsockopt(socket.SOL_SOCKET, _EXCLUSIVE, 1)
    return sock


def bind_player_socket(port: int) -> socket.socket:
    """The listening socket on ``127.0.0.1:port``; ``OSError`` when anyone else holds the port."""
    with _exclusive_socket() as probe:  # someone on 0.0.0.0:port (or 127.0.0.1:port) → busy
        probe.bind(("0.0.0.0", port))
    sock = _exclusive_socket()
    try:
        sock.bind((HOST, port))
        sock.listen(128)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


class PlayerListener:
    """Owns the player app's uvicorn server for the lifetime of the main app."""

    def __init__(self, port: int, quiz: QuizService) -> None:
        self.port = port
        self.quiz = quiz
        self.app: Optional[FastAPI] = None
        self.server: Optional[_EmbeddedServer] = None
        self.task: Optional[asyncio.Task[None]] = None

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.started and self.task is not None and not self.task.done()

    async def start(self) -> bool:
        """Bind and serve; ``False`` (logged) when disabled, busy or not up in time — never raises."""
        if not self.port:
            logger.info("ℹ️ quiz player listener off (quiz.public_port = 0)")
            return False
        try:
            sock = bind_player_socket(self.port)
        except (OSError, OverflowError) as exc:
            logger.error(
                "❌ quiz player listener: %s:%d is busy or unusable (%s) — the public quiz is OFF; "
                "the main app keeps serving. Free the port (only the player app may use it) and restart.",
                HOST, self.port, exc,
            )
            return False
        self.app = create_player_app(self.quiz)
        quiet_player_requests()
        config = uvicorn.Config(
            self.app,
            lifespan="off",
            log_config=None,  # the process's logging is already configured; don't reset it
            access_log=True,  # False would empty the shared uvicorn.access logger (see the module docstring)
            # cloudflared and Funnel forward from loopback with the phone's public IP as the LAST
            # X-Forwarded-For entry (see ratelimit.py); trusted from 127.0.0.1 only, uvicorn takes the
            # rightmost untrusted entry, so request.client is the phone whatever the phone sent.
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
            ws_ping_interval=20.0,
            ws_ping_timeout=20.0,
        )
        self.server = _EmbeddedServer(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]), name="quiz-player-listener")
        self.task.add_done_callback(self._on_done)
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT_S
        while not self.server.started and not self.task.done():
            if asyncio.get_running_loop().time() > deadline:
                logger.error("❌ quiz player listener: not serving %s:%d after %.0f s", HOST, self.port, START_TIMEOUT_S)
                await self.stop()
                return False
            await asyncio.sleep(0.02)
        if not self.running:
            return False
        logger.info("✅ quiz player listener on http://%s:%d/play (public only through the tunnel)", HOST, self.port)
        return True

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("❌ quiz player listener crashed: %r", exc, exc_info=exc)

    async def stop(self) -> None:
        """Ask the server to finish; force it after ``STOP_TIMEOUT_S``."""
        if self.app is not None:
            self.app.state.detach()  # the quiz stops pushing to this app's sockets
            self.app = None
        if self.server is None or self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=STOP_TIMEOUT_S)
        except TimeoutError:
            logger.warning("⚠️ quiz player listener: no clean stop in %.0f s — forcing it", STOP_TIMEOUT_S)
            self.server.force_exit = True
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        except Exception:  # noqa: BLE001 — already logged by _on_done
            pass
        logger.info("👋 quiz player listener stopped")
        self.server = None
        self.task = None
