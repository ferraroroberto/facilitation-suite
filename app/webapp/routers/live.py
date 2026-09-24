"""The live session: ``/ws`` (snapshots out, intents in) and its REST twins."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.live.actions import catalog, run_action
from src.live.hub import LiveError, LiveHub

logger = logging.getLogger(__name__)
router = APIRouter()


def _hub(request: Request) -> LiveHub:
    return request.app.state.live


def _err(exc: LiveError) -> AppError:
    return AppError(exc.status, exc.code, str(exc))


class Activate(BaseModel):
    session: str = Field(min_length=1, max_length=40)


class ActionBody(BaseModel):
    action: str = Field(min_length=1, max_length=60)
    arg: Optional[str] = Field(None, max_length=60)


def _both(hub: LiveHub) -> dict[str, Any]:
    snap = hub.snapshot()
    return {"plan": hub.plan_message(), "state": snap["state"], "server_now": snap["server_now"]}


@router.get("/api/live")
async def live_state(request: Request) -> dict[str, Any]:
    return _both(_hub(request))


@router.post("/api/live/activate")
async def activate(request: Request, body: Activate) -> dict[str, Any]:
    hub = _hub(request)
    try:
        hub.activate(body.session)
    except LiveError as exc:
        raise _err(exc) from exc
    return _both(hub)


@router.post("/api/live/deactivate")
async def deactivate(request: Request) -> dict[str, Any]:
    _hub(request).deactivate()
    return {"active": False}


@router.post("/api/live/action")
async def action(request: Request, body: ActionBody) -> dict[str, Any]:
    try:
        return run_action(_hub(request), body.action, body.arg)
    except LiveError as exc:
        raise _err(exc) from exc


@router.get("/api/live/actions")
def actions() -> dict[str, Any]:
    return {"actions": catalog()}


@router.get("/api/live/theme.css", include_in_schema=False)
def session_theme(request: Request) -> Response:
    """The live session's own ``theme.css`` (empty when it has none)."""
    folder = _hub(request).folder
    css = ""
    if folder is not None and (folder / "theme.css").is_file():
        try:
            css = (folder / "theme.css").read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("⚠️ session theme.css unreadable: %s", exc)
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})


async def _pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            message = await queue.get()
            await ws.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass  # the receive loop notices the closed socket and disconnects the client


@router.websocket("/ws")
async def live_socket(ws: WebSocket, role: str = "app") -> None:
    await ws.accept()
    hub: LiveHub = ws.app.state.live
    client = hub.connect(role)
    sender = asyncio.create_task(_pump(ws, client.queue))
    try:
        while True:
            try:
                msg = await ws.receive_json()
            except (ValueError, KeyError):
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "hello":
                try:
                    hub.hello(client, int(msg.get("w") or 0), int(msg.get("h") or 0))
                except (TypeError, ValueError):
                    continue
            elif kind == "action":
                try:
                    run_action(hub, str(msg.get("action") or ""), msg.get("arg"))
                except LiveError as exc:
                    client.push({"type": "error", "code": exc.code, "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.disconnect(client)
