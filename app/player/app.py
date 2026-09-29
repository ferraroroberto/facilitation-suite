"""The quiz player app — the only thing the public internet can reach (#34, #49, #52).

Served on ``127.0.0.1:<quiz.public_port>`` (default 8451) by
``app/player/listener.py`` and published by a Cloudflare named tunnel
(``quiz.<domain>``, ``src/tunnel.py``; Tailscale Funnel is the fallback). It
mounts **nothing** from the main :8449 app: no ``RemoteAuth`` bypass, no
``/api/*``, no ``/ws``, no presenter or stage, not the main ``/static`` (its
own page chrome — the fleet tokens and a few vendored stylesheets — is copied
into ``app/player/static/``), and no OpenAPI docs. Every route lives under
``/play``; anything else is a 404.

It talks to the **same** ``QuizService`` as the main app (passed in by the
listener, which runs on the main app's loop), so every route is ``async``:

    GET  /play                → the phone page (``?pin=`` fills the PIN in)
    GET  /play/static/…       → its CSS and JS (``app/player/static/``)
    GET  /play/api/ping       → {"ok": true, "time": <server epoch seconds>}
    POST /play/api/join       {pin, nickname, key?} → {state: joined | wrong_pin |
                              no_game | closed, player_id, secret, name, …, view}
    POST /play/api/resume     {player_id, secret} → {state: resumed |
                              unknown_player | kicked, …, view}
    POST /play/api/answer     {player_id, secret, item_id, choice, elapsed_ms}
                              → the engine's ack {state: accepted | duplicate |
                              too_late | not_open | unknown_player | kicked, …},
                              or ``no_game`` while no session is live (after a
                              restart, until the presenter goes live): retry it
    GET  /play/api/state      ?player_id&secret → the view message (polling)
    WS   /play/ws             → {"op": "hello", player_id, secret} in, the same
                              view message pushed on every change; {"op":
                              "ping"} → {"type": "pong"}

The **view message** is ``{"type": "view", "state": ok | unknown_player |
no_game, "view": QuizService.player_view(…) | null, "now_ms"}`` — a phone's
own view only: never the plan's options (they hold the correct answers),
never other players' answers. ``answer_trusted`` is not reachable from here.

Join, resume and answers are rate-limited per client IP (``ratelimit.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.player import ratelimit
from app.player.push import Conn, PlayerPush
from app.webapp.errors import error_response
from src.errors import DomainError
from src.live.hub import now_ms
from src.quiz.engine import JOINED, RESUMED
from src.quiz.service import WRONG_PIN, QuizService

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PAGE = HERE / "play.html"
STATIC_DIR = HERE / "static"
PREFIX = "/play"
HELLO_TIMEOUT_S = 10.0  # a socket that does not say who it is by then is closed
MAX_SOCKETS = 500
LOG_REFUSALS_EVERY_S = 60.0


class PlayerError(DomainError):
    """A refusal of the public player API (rate limit, too many sockets)."""


class NoCacheStaticFiles(StaticFiles):
    """``StaticFiles`` that always revalidates, so a phone never keeps a stale script after a restart."""

    def file_response(self, full_path: os.PathLike[str], stat_result: os.stat_result, scope: Scope,
                      status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


class JoinBody(BaseModel):
    pin: str = Field(max_length=12)
    nickname: str = Field(max_length=60)
    key: Optional[str] = Field(default=None, max_length=64)  # the phone's idempotency key for retries


class PlayerBody(BaseModel):
    player_id: str = Field(min_length=1, max_length=40)
    secret: str = Field(min_length=1, max_length=64)


class AnswerBody(PlayerBody):
    item_id: str = Field(min_length=1, max_length=80)
    choice: int = Field(ge=1, le=4)
    elapsed_ms: Optional[int] = Field(default=None, ge=0, le=3_600_000)


def create_player_app(quiz: QuizService) -> FastAPI:
    """The public player app over the main app's ``quiz`` service: ``/play`` routes only, no docs."""
    app = FastAPI(title="facilitation-suite player", docs_url=None, redoc_url=None, openapi_url=None)
    limiter = ratelimit.RateLimiter()
    refused_logged: dict[tuple[str, str], float] = {}

    def view_message(player_id: str, secret: str) -> dict[str, Any]:
        if quiz.live.session_id is None:
            return {"type": "view", "state": "no_game", "view": None}
        view = quiz.player_view(player_id, secret)
        return {"type": "view", "state": "ok" if view is not None else "unknown_player", "view": view}

    push = PlayerPush(view_message)
    app.state.push = push
    app.state.limiter = limiter
    app.state.detach = lambda: quiz.change_listeners.remove(push.notify)  # the listener calls it on stop
    quiz.change_listeners.append(push.notify)

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def limit(name: str, ip: str, rule: ratelimit.Limit, *, take: bool = True) -> None:
        wait = limiter.allow(name, ip, rule) if take else limiter.peek(name, ip, rule)
        if not wait:
            return
        now = time.monotonic()
        if now - refused_logged.get((name, ip), -LOG_REFUSALS_EVERY_S) >= LOG_REFUSALS_EVERY_S:
            if len(refused_logged) > 1000:
                refused_logged.clear()
            refused_logged[(name, ip)] = now
            logger.warning("⚠️ quiz player: %s rate limit reached for %s (logged once a minute)", name, ip)
        raise PlayerError(429, "slow_down", "Too many tries — wait a moment", {"retry_after_s": round(wait, 1)})

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> Response:
        return error_response(exc.status, exc.code, str(exc), exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        return error_response(422, "validation_error", "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return error_response(exc.status_code, "not_found" if exc.status_code == 404 else "http_error", str(exc.detail))

    @app.get(PREFIX, include_in_schema=False)
    async def play() -> FileResponse:
        return FileResponse(PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})

    app.mount(f"{PREFIX}/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="play-static")

    @app.get(f"{PREFIX}/api/ping")
    async def ping() -> dict[str, object]:
        return {"ok": True, "time": time.time()}

    @app.post(f"{PREFIX}/api/join")
    async def join(request: Request, body: JoinBody) -> dict[str, Any]:
        ip = client_ip(request)
        limit("wrong_pin", ip, ratelimit.WRONG_PIN, take=False)
        limit("join", ip, ratelimit.JOIN)
        res = quiz.join_pin(body.pin, body.nickname, body.key)
        if res.state == WRONG_PIN:
            limiter.allow("wrong_pin", ip, ratelimit.WRONG_PIN)
        out = res.as_dict()
        if res.state == JOINED:
            out["view"] = view_message(res.player_id, res.secret)
        return {**out, "now_ms": now_ms()}

    @app.post(f"{PREFIX}/api/resume")
    async def resume(request: Request, body: PlayerBody) -> dict[str, Any]:
        limit("join", client_ip(request), ratelimit.JOIN)
        if quiz.live.session_id is None:
            return {"state": "no_game", "now_ms": now_ms()}
        res = quiz.resume(body.player_id, body.secret)
        out = res.as_dict()
        if res.state == RESUMED:
            out["view"] = view_message(body.player_id, body.secret)
        return {**out, "now_ms": now_ms()}

    @app.post(f"{PREFIX}/api/answer")
    async def answer(request: Request, body: AnswerBody) -> dict[str, Any]:
        limit("answer", client_ip(request), ratelimit.ANSWER)
        if quiz.live.session_id is None:  # after a restart, until the presenter goes live: retry, keep the identity
            return {"state": "no_game", "item_id": body.item_id, "choice": None, "elapsed_ms": None, "now_ms": now_ms()}
        res = quiz.answer(body.player_id, body.secret, body.item_id, body.choice, body.elapsed_ms)
        return {**res.as_dict(), "now_ms": now_ms()}

    @app.get(f"{PREFIX}/api/state")
    async def state(player_id: str = "", secret: str = "") -> dict[str, Any]:
        return {**view_message(player_id[:40], secret[:64]), "now_ms": now_ms()}

    @app.websocket(f"{PREFIX}/ws")
    async def socket(ws: WebSocket) -> None:
        if len(push.conns) >= MAX_SOCKETS:
            await ws.close(code=1013)  # try again later: the phone polls meanwhile
            return
        await ws.accept()
        conn: Optional[Conn] = None
        try:
            first = True
            while True:
                raw = await (asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S) if first else ws.receive_text())
                first = False
                try:
                    msg = json.loads(raw)
                except ValueError:
                    msg = {}
                op = msg.get("op") if isinstance(msg, dict) else None
                if op == "hello":
                    if conn is not None:
                        push.conns.discard(conn)
                    conn = Conn(ws, str(msg.get("player_id") or "")[:40], str(msg.get("secret") or "")[:64])
                    push.add(conn)
                    await push.send(conn, force=True)
                elif op == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "t": msg.get("t"), "now_ms": now_ms()}))
                elif conn is None:
                    await ws.close(code=1008)  # the first message must say who this is
                    return
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass  # gone, silent past the hello timeout, or closed under a send
        finally:
            if conn is not None:
                push.conns.discard(conn)

    return app
