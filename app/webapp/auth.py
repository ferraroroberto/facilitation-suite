"""The gate for other devices (the phone remote over the tailnet, epic §3.2).

This PC is always let in (``errors.is_local``). Any other device needs the
bearer token from ``config.json`` → ``remote.token`` — in an ``Authorization:
Bearer`` header, a ``?token=`` query, or the ``fs_remote`` cookie. A valid
``?token=`` also sets that cookie (HttpOnly, SameSite=Strict, 30 days), so the
phone opens the pairing link once and every later page, fetch and WebSocket
carries it by itself. A new token in Settings unpairs every phone.

Pure ASGI, so WebSockets (``/ws``) go through the same gate as HTTP. Static
files, ``/healthz`` and the ``/remote`` page itself stay open, so an unpaired
phone can load the page and be told how to pair.

A browser names the page behind each request in ``Origin``. Only the app's own
pages may open the socket or send a state change, whoever the caller is: a page
of any other site open in this PC's browser shares this PC's address. A caller
that sends no ``Origin`` is not a browser page (the Stream Deck plugin, a
script) and goes through the gate above as before.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from app.webapp.errors import is_local

logger = logging.getLogger(__name__)

COOKIE = "fs_remote"
COOKIE_MAX_AGE_S = 30 * 24 * 3600
OPEN_EXACT = frozenset({"/healthz", "/remote", "/manifest.webmanifest", "/favicon.ico"})
OPEN_PREFIXES = ("/static/", "/themes/")
TOKEN_IN_URL = re.compile(r"(token=)[^&\s\"']+")
SERVER_LOGGERS = ("uvicorn.access", "uvicorn.error")  # request lines, WebSocket handshakes
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})  # the browser keeps another site's page from reading the answer


class RedactTokens(logging.Filter):
    """Keep the pairing link's ``?token=`` out of the server's request log."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact inside the arguments and keep their shape: uvicorn's access
        # formatter unpacks them (client, method, path, version, status).
        def clean(v: Any) -> Any:
            return TOKEN_IN_URL.sub(r"\1<redacted>", v) if isinstance(v, str) and "token=" in v else v

        record.msg = clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: clean(v) for k, v in record.args.items()}
        return True


def redact_server_logs() -> None:
    for name in SERVER_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in lg.filters):
            lg.addFilter(RedactTokens())


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def foreign_origin(scope: dict[str, Any]) -> bool:
    """A socket or a state change sent by a page that is not one of this app's own."""
    if scope["type"] == "http" and scope.get("method", "GET") in READ_METHODS:
        return False
    headers = _headers(scope)
    origin = headers.get("origin")
    if origin is None:
        return False
    host = headers.get("host", "").lower()
    return not host or urlsplit(origin).netloc.lower() != host


def presented_token(scope: dict[str, Any]) -> tuple[str, bool]:
    """The token a request carries, and whether it came in the query string."""
    headers = _headers(scope)
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    if query.get("token"):
        return query["token"][0].strip(), True
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:  # noqa: BLE001 — a malformed cookie header is just no token
        return "", False
    return (cookie[COOKIE].value if COOKIE in cookie else ""), False


class RemoteAuth:
    def __init__(self, app: Any, get_token: Callable[[], str]) -> None:
        self.app = app
        self.get_token = get_token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if foreign_origin(scope):
            await self._refuse(scope, send, 403, 4403, "foreign_origin", "Only this app's own pages may do this")
            return
        if is_local(scope):
            await self.app(scope, receive, send)
            return
        token = (self.get_token() or "").strip()
        presented, from_query = presented_token(scope)
        if token and presented and hmac.compare_digest(presented.encode(), token.encode()):
            if from_query and scope["type"] == "http":
                send = self._with_cookie(send, token, scope.get("scheme") == "https")
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in OPEN_EXACT or path.startswith(OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        code, message = ("remote_off", "The phone remote is off — create its link in Settings on the PC") if not token else \
            ("remote_token_required", "This device is not paired — open the phone link from Settings on the PC")
        await self._refuse(scope, send, 401, 4401, code, message,
                           extra_headers=[(b"www-authenticate", b'Bearer realm="facilitation-suite"')])

    @staticmethod
    async def _refuse(scope: dict[str, Any], send: Any, status: int, close_code: int, code: str, message: str,
                      extra_headers: Optional[list[tuple[bytes, bytes]]] = None) -> None:
        client = (scope.get("client") or ("?",))[0]
        logger.info("ℹ️ refused %s %s from %s (%s)", scope["type"], scope.get("path", ""), client, code)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": close_code})
            return
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *(extra_headers or [])]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _with_cookie(send: Any, token: str, secure: bool) -> Any:
        value = f"{COOKIE}={token}; Path=/; Max-Age={COOKIE_MAX_AGE_S}; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")

        async def wrapped(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), (b"set-cookie", value.encode("latin-1"))]}
            await send(message)

        return wrapped
