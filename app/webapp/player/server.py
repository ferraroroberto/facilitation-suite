"""The quiz player app — the only thing the public internet can reach.

Served on ``127.0.0.1:<quiz.public_port>`` (default 8451) by
``app/webapp/player/listener.py`` and published by a Cloudflare named tunnel
(``quiz.<domain>``, ``src/tunnel.py``; Tailscale Funnel is the fallback). It
mounts **nothing** from the main :8449 app: no ``RemoteAuth`` bypass, no
``/api/*``, no ``/ws``, no presenter or stage, not the main ``/static`` (its
own page chrome — the fleet tokens and a few vendored stylesheets — is copied
into ``app/webapp/player/static/``), and no OpenAPI docs. Every route lives under
``/play``; anything else is a 404.

It talks to the **same** ``QuizService`` and ``RoomPolls`` as the main app
(passed in by the listener, which runs on the main app's loop), so every route
is ``async``:

    GET  /play                → the phone page (``?pin=`` fills the PIN in)
    GET  /play/static/…       → its CSS and JS (``app/webapp/player/static/``)
    GET  /play/api/ping       → {"ok": true, "time": <server epoch seconds>, "lang":
                              the live session's language, so the join screen
                              speaks it before the phone has a view,
                              "name_required": a quiz is on stage, whose players
                              need a nickname}
    POST /play/api/join       {pin, nickname, key?} → {state: joined | wrong_pin |
                              no_game | closed, player_id, secret, name, …, view}.
                              With a quiz on stage it joins the room and the game
                              (the nickname is required); with none it joins the
                              room only, the name optional. The same ``key``
                              later, with a nickname, takes that member into a
                              quiz (the room view's ``quiz_join``).
    POST /play/api/resume     {player_id, secret} → {state: resumed |
                              unknown_player | kicked, …, view}
    POST /play/api/poll       {player_id, secret, item_id, value, key?} → the
                              room poll's ack {state: accepted | not_open |
                              unknown_player | closed, item_id, answer}
                              (``src/room/polls.py``), ``no_game`` while no
                              session is live: retry it with the same key
    POST /play/api/answer     {player_id, secret, item_id, choice, elapsed_ms}
                              → the engine's ack {state: accepted | duplicate |
                              too_late | not_open | unknown_player | kicked, …},
                              or ``no_game`` while no session is live (after a
                              restart, until the presenter goes live): retry it
    GET  /play/api/state      ?player_id&secret → the view message (polling)
    GET  /play/form?t=        → the feedback form's page (``form.html``): 200 while
                              the form is open, 410 with the same page (which
                              says "this form is closed") otherwise
    GET  /play/api/form?t=    → {state: open, form: {title, intro, questions},
                              lang} or {state: closed, lang}
    POST /play/api/form       {t, name, answers: {question id: value}, key?,
                              phone?} → {state: accepted, response_id}; 410
                              ``closed``; 422 ``bad_answers`` with
                              ``detail.errors`` per question
                              (``src/forms/service.py``). No answer is ever
                              sent back out, to this phone or any other.
    WS   /play/ws             → {"op": "hello", player_id, secret} in, the same
                              view message pushed on every change; {"op":
                              "ping"} → {"type": "pong"}

The **view message** is ``{"type": "view", "state": ok | unknown_player |
no_game, "view": …, "now_ms"}`` — a phone's own view only: never the plan's
options (they hold the correct answers), never other players' answers. The
view is the room poll on stage (``RoomPolls.view``, ``kind: poll``), else the
phone's quiz (``QuizService.player_view``), else an open feedback form on
stage (``FormsService.room_view``, ``kind: form``: its title and the form
page's link), else ``{"kind": "room", "quiz_join", "lang"}`` for a room member with nothing to answer
(``quiz_join``: a quiz on stage that this member has not joined yet).
``answer_trusted`` is not reachable from here.

Join, resume, answers and poll answers are rate-limited per client IP, form
submissions per client IP and per phone (``ratelimit.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
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

from app.webapp.errors import error_response
from app.webapp.player import ratelimit
from app.webapp.player.push import Conn, PlayerPush
from src.errors import DomainError
from src.forms.model import MAX_QUESTIONS, FormError
from src.forms.service import FormsService
from src.live.hub import now_ms
from src.quiz.engine import JOINED, RESUMED, UNKNOWN_PLAYER
from src.quiz.service import WRONG_PIN, QuizService
from src.request_timing import RequestTimingMiddleware
from src.room.model import RESUMED as ROOM_RESUMED
from src.room.polls import RoomPolls
from src.room.service import RoomJoin

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PAGE = HERE / "play.html"
FORM_PAGE = HERE / "form.html"
STATIC_DIR = HERE / "static"
PREFIX = "/play"
HELLO_TIMEOUT_S = 10.0  # a socket that does not say who it is by then is closed
# Open sockets at once, counted from the accept (a phone that has not said hello yet holds one too).
# Five times the 60-player target; it stays under the listener's connection cap (``listener.py``),
# which keeps the player side within what the main app's shared loop can watch.
MAX_SOCKETS = 300
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


class PollBody(PlayerBody):
    item_id: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=200)
    key: Optional[str] = Field(default=None, max_length=64)  # the phone's retry key


class FormBody(BaseModel):
    t: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=60)
    answers: dict[str, str | int | float | list[str]] = Field(default_factory=dict, max_length=MAX_QUESTIONS)
    key: Optional[str] = Field(default=None, max_length=64)  # the phone's retry key
    phone: Optional[str] = Field(default=None, max_length=64)  # the phone's own id, for its rate limit


class AnswerBody(PlayerBody):
    item_id: str = Field(min_length=1, max_length=80)
    choice: int = Field(ge=1, le=4)
    elapsed_ms: Optional[int] = Field(default=None, ge=0, le=3_600_000)


def create_player_app(quiz: QuizService, polls: Optional[RoomPolls] = None,
                      forms: Optional[FormsService] = None) -> FastAPI:
    """The public player app over the main app's ``quiz``, room ``polls`` and feedback ``forms``:
    ``/play`` routes only, no docs."""
    app = FastAPI(title="facilitation-suite player", docs_url=None, redoc_url=None, openapi_url=None)
    # A room of phones on one game: each answer carries Server-Timing and a slow one is logged.
    app.add_middleware(RequestTimingMiddleware, timed_prefix=f"{PREFIX}/api/")
    limiter = ratelimit.RateLimiter()
    refused_logged: dict[tuple[str, str], float] = {}
    view_message = _view_messages(quiz, polls, forms)
    push = _attach_push(app, quiz, polls, view_message)
    sockets: set[WebSocket] = set()  # every accepted socket, said hello or not
    app.state.sockets = sockets
    app.state.limiter = limiter

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

    _install_error_handlers(app)

    @app.get(PREFIX, include_in_schema=False)
    async def play() -> FileResponse:
        return FileResponse(PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})

    app.mount(f"{PREFIX}/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="play-static")

    @app.get(f"{PREFIX}/api/ping")
    async def ping() -> dict[str, object]:
        return {"ok": True, "time": time.time(), "lang": quiz.language(),
                "name_required": polls is None or quiz.game_on_stage() is not None}

    @app.post(f"{PREFIX}/api/join")
    async def join(request: Request, body: JoinBody) -> dict[str, Any]:
        ip = client_ip(request)
        limit("wrong_pin", ip, ratelimit.WRONG_PIN, take=False)
        limit("join", ip, ratelimit.JOIN)
        if polls is not None and quiz.game_on_stage() is None and quiz.pin_matches(body.pin):
            return _join_room(quiz, body, view_message)  # no quiz on stage: the room only, a name optional
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
        if res.state == UNKNOWN_PLAYER and polls is not None:  # a room member who plays no quiz
            member = quiz.room.resume(body.player_id, body.secret)
            if member.state == ROOM_RESUMED:
                return {**_member_dict(member, RESUMED), "view": view_message(body.player_id, body.secret),
                        "now_ms": now_ms()}
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

    @app.post(f"{PREFIX}/api/poll")
    async def poll(request: Request, body: PollBody) -> dict[str, Any]:
        limit("answer", client_ip(request), ratelimit.ANSWER)
        if polls is None:
            raise PlayerError(404, "not_found", "No room polls here")
        if quiz.live.session_id is None:  # after a restart, until the presenter goes live: retry with the same key
            return {"state": "no_game", "item_id": body.item_id, "answer": None, "now_ms": now_ms()}
        res = polls.answer(body.player_id, body.secret, body.item_id, body.value, body.key)
        return {**res.as_dict(), "now_ms": now_ms()}

    @app.get(f"{PREFIX}/api/state")
    async def state(player_id: str = "", secret: str = "") -> dict[str, Any]:
        return {**view_message(player_id[:40], secret[:64]), "now_ms": now_ms()}

    if forms is not None:
        _form_routes(app, quiz, forms, client_ip, limit)

    @app.websocket(f"{PREFIX}/ws")
    async def socket(ws: WebSocket) -> None:
        await _serve_socket(ws, push, sockets)

    return app


ViewMessage = Callable[[str, str], dict[str, Any]]


def _view_messages(quiz: QuizService, polls: Optional[RoomPolls], forms: Optional[FormsService] = None) -> ViewMessage:
    """(player_id, secret) → that phone's view message (see the module docstring)."""

    def view_message(player_id: str, secret: str) -> dict[str, Any]:
        if quiz.live.session_id is None:
            return {"type": "view", "state": "no_game", "view": None}
        view = polls.view(player_id, secret) if polls is not None else None
        if view is None:
            view = quiz.player_view(player_id, secret)
        if view is None and polls is not None and not polls.refused(player_id, secret):
            game = quiz.game_on_stage()
            view = (forms.room_view() if forms is not None else None) or {
                "kind": "room", "lang": quiz.language(),
                "quiz_join": game is not None and game.phase != "podium" and player_id not in game.players}
        return {"type": "view", "state": "ok" if view is not None else "unknown_player", "view": view}

    return view_message


def _attach_push(app: FastAPI, quiz: QuizService, polls: Optional[RoomPolls], view_message: ViewMessage) -> PlayerPush:
    """The phones' push, run on every quiz change and, with room polls, on every live-state push
    (a poll opens, closes or leaves the stage); ``app.state.detach`` undoes both (the listener calls it on stop)."""
    push = PlayerPush(view_message)
    app.state.push = push
    quiz.change_listeners.append(push.notify)
    if polls is not None:
        quiz.live.push_listeners.append(push.notify)

    def detach() -> None:
        quiz.change_listeners.remove(push.notify)
        if polls is not None:
            quiz.live.push_listeners.remove(push.notify)

    app.state.detach = detach
    return push


Limiter = Callable[..., None]


def _form_routes(app: FastAPI, quiz: QuizService, forms: FormsService, client_ip: Callable[[Request], str],
                 limit: Limiter) -> None:
    """The feedback form's page, its questions and its submit (see the module docstring)."""

    @app.get(f"{PREFIX}/form", include_in_schema=False)
    async def form_page(t: str = "") -> FileResponse:
        try:
            forms.form_for(t[:40])
            status = 200
        except FormError:
            status = 410  # the same page, which says the form is closed
        return FileResponse(FORM_PAGE, status_code=status, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get(f"{PREFIX}/api/form")
    async def form_questions(t: str = "") -> dict[str, Any]:
        try:
            _, form = forms.form_for(t[:40])
        except FormError:
            return {"state": "closed", "lang": quiz.language()}
        return {"state": "open", "form": form, "lang": quiz.language()}

    @app.post(f"{PREFIX}/api/form")
    async def form_submit(request: Request, body: FormBody) -> dict[str, Any]:
        limit("form", client_ip(request), ratelimit.FORM)
        if body.phone:
            limit("form_phone", body.phone, ratelimit.FORM_PHONE)
        return forms.submit(body.t, body.name, body.answers, body.key)


def _install_error_handlers(app: FastAPI) -> None:
    """Every refusal in the one JSON error envelope the phone reads."""

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> Response:
        return error_response(exc.status, exc.code, str(exc), exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        return error_response(422, "validation_error", "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return error_response(exc.status_code, "not_found" if exc.status_code == 404 else "http_error", str(exc.detail))


def _member_dict(member: RoomJoin, state: str) -> dict[str, Any]:
    """A room member's join or resume answer, shaped like the quiz's (no game)."""
    return {"state": state, "game_id": None, "player_id": member.player_id, "secret": member.secret, "name": member.name}


def _join_room(quiz: QuizService, body: JoinBody, view_message: ViewMessage) -> dict[str, Any]:
    """Join the session room only (no quiz on stage): the name may be empty."""
    member = quiz.room.join(body.nickname, body.key)
    out = _member_dict(member, member.state)
    if member.state == JOINED:
        out["view"] = view_message(str(member.player_id), str(member.secret))
    return {**out, "now_ms": now_ms()}


async def _serve_socket(ws: WebSocket, push: PlayerPush, sockets: set[WebSocket]) -> None:
    """One phone's socket: the hello that says who it is, then pings until it goes away."""
    if len(sockets) >= MAX_SOCKETS:
        await ws.close(code=1013)  # try again later: the phone polls meanwhile
        return
    sockets.add(ws)
    conn: Optional[Conn] = None
    try:
        await ws.accept()
        hello_by = time.monotonic() + HELLO_TIMEOUT_S
        while True:
            if conn is None:  # pings are answered meanwhile, but the hello deadline keeps running
                raw = await asyncio.wait_for(_receive_text(ws), max(0.0, hello_by - time.monotonic()))
            else:
                raw = await _receive_text(ws)
            if raw is None:
                await ws.close(code=1003)  # the page only ever sends text frames
                return
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
        sockets.discard(ws)
        if conn is not None:
            push.conns.discard(conn)


async def _receive_text(ws: WebSocket) -> Optional[str]:
    """The next text frame's text; ``None`` for a frame that carries none (a binary one)."""
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
    return message.get("text")
