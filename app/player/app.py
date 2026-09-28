"""The quiz player app — the only thing the public internet can reach (#34, #49).

Served on ``127.0.0.1:<quiz.public_port>`` (default 8450) by
``app/player/listener.py`` and published by Tailscale Funnel on :8443. It
mounts **nothing** from the main :8449 app: no ``RemoteAuth`` bypass, no
``/api/*``, no ``/ws``, no presenter or stage, no shared static mount, and no
OpenAPI docs. Every route lives under ``/play``; anything else is a 404.

For now (Step 1/8, the ingress spike) it serves:

    GET /play           → a hello-world page that pings and echoes over WS
    GET /play/api/ping  → {"ok": true, "time": <server epoch seconds>}
    WS  /play/ws        → echo (text in, the same text out)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

PAGE = Path(__file__).resolve().parent / "play.html"
PREFIX = "/play"


def create_player_app() -> FastAPI:
    """The public player app: ``/play`` routes only, no docs, no shared state yet."""
    app = FastAPI(title="facilitation-suite player", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get(PREFIX, include_in_schema=False)
    async def play() -> FileResponse:
        return FileResponse(PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get(f"{PREFIX}/api/ping")
    async def ping() -> dict[str, object]:
        return {"ok": True, "time": time.time()}

    @app.websocket(f"{PREFIX}/ws")
    async def echo(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                await ws.send_text(await ws.receive_text())
        except WebSocketDisconnect:
            pass

    return app
