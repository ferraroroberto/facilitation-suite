"""The player sockets (#52): each phone's own view, pushed when its game changes.

``QuizService`` runs ``notify`` after every record (a join, an answer, a
phase, a kick) and when the live session changes. Bursts coalesce: one flush
at most every ``FLUSH_S``, and each socket gets its view only when it
differs from the last one it got — 60 answers in a second is a handful of
small sends. Every view comes from ``QuizService.player_view``, never from the
plan (the plan's options carry the correct answers).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import WebSocket

from src.live.hub import now_ms

logger = logging.getLogger(__name__)

FLUSH_S = 0.05  # coalesce a burst of changes into one push
SEND_TIMEOUT_S = 5.0  # a phone that cannot take a message this long is dropped (it reconnects or polls)


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    player_id: str
    secret: str
    last: str = ""


class PlayerPush:
    def __init__(self, view: Callable[[str, str], dict[str, Any]]) -> None:
        self.view = view  # (player_id, secret) → the message for that phone
        self.conns: set[Conn] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # the loop the sockets live on
        self._scheduled = False
        self._task: Optional[asyncio.Task[None]] = None

    def add(self, conn: Conn) -> None:
        self.loop = asyncio.get_running_loop()
        self.conns.add(conn)

    def notify(self) -> None:
        """A game changed: push soon, once for the whole burst (safe from any thread)."""
        if self._scheduled or not self.conns or self.loop is None:
            return
        self._scheduled = True
        self.loop.call_soon_threadsafe(self.loop.call_later, FLUSH_S, self._start_flush)

    def _start_flush(self) -> None:
        self._scheduled = False
        self._task = asyncio.create_task(self.flush(), name="quiz-player-push")

    async def flush(self) -> None:
        await asyncio.gather(*(self.send(c) for c in list(self.conns)))

    async def send(self, conn: Conn, *, force: bool = False) -> None:
        """This phone's view, if it changed since the last one it got (or ``force``)."""
        msg = self.view(conn.player_id, conn.secret)
        body = json.dumps(msg, separators=(",", ":"))
        if body == conn.last and not force:
            return
        conn.last = body
        try:  # the server clock rides along so the phone can count the time left down
            await asyncio.wait_for(conn.ws.send_text(json.dumps({**msg, "now_ms": now_ms()})), SEND_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — a gone or stuck phone: drop it, it reconnects or polls
            logger.info("ℹ️ quiz player socket %s dropped (%s)", conn.player_id, type(exc).__name__)
            self.conns.discard(conn)
            with contextlib.suppress(Exception):
                await conn.ws.close()
