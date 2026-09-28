"""The live session: ``/ws`` (snapshots out, intents in), activation, map fixes and
frozen captures. REST intents (the Stream Deck) are ``routers/actions.py``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.activities.registry import ACTIVITIES_DIR, editors
from src.errors import DomainError
from src.live.actions import run_action
from src.live.hub import LiveHub
from src.sessions.model import ITEM_ID
from src.sessions.store import SessionError
from src.sessions.theme import theme_css

logger = logging.getLogger(__name__)
router = APIRouter()

PLUGIN_ASSETS = {"stage.js": "text/javascript", "stage.css": "text/css", "world.svg": "image/svg+xml"}


def _hub(request: Request) -> LiveHub:
    return request.app.state.live


class Activate(BaseModel):
    session: str = Field(min_length=1, max_length=40)


class PlaceBody(BaseModel):
    message_id: int = Field(ge=1)
    geonameid: str = Field(min_length=1, max_length=20, pattern=r"^(\d+|country:[A-Z]{2})$")


def _both(hub: LiveHub) -> dict[str, Any]:
    snap = hub.snapshot()
    return {"plan": hub.plan_message(), "state": snap["state"], "server_now": snap["server_now"]}


@router.get("/api/live")
async def live_state(request: Request) -> dict[str, Any]:
    return _both(_hub(request))


@router.post("/api/live/activate")
async def activate(request: Request, body: Activate) -> dict[str, Any]:
    hub = _hub(request)
    hub.activate(body.session)
    return _both(hub)


@router.post("/api/live/deactivate")
async def deactivate(request: Request) -> dict[str, Any]:
    _hub(request).deactivate()
    return {"active": False}


@router.post("/api/live/place")
async def place(request: Request, body: PlaceBody) -> dict[str, Any]:
    """Put an unplaced map answer where it belongs (the presenter's fix)."""
    from src.geo.gazetteer import gazetteer

    hub = _hub(request)
    if hub.session_id is None:
        raise AppError(409, "not_live", "No session is live")
    found = await asyncio.to_thread(lambda: gazetteer().by_geonameid(body.geonameid))
    if found is None:
        raise AppError(404, "unknown_place", "No such place")
    request.app.state.capture.place(body.message_id, body.geonameid)
    return {"placed": found.as_dict()}


@router.get("/api/live/captures/{item_id}")
def frozen_capture(request: Request, item_id: str) -> dict[str, Any]:
    """A frozen capture (answers + result + the item as it was)."""
    if not ITEM_ID.fullmatch(item_id):
        raise AppError(404, "not_found", "No such capture")
    data = request.app.state.capture.frozen(item_id)
    if data is None:
        raise AppError(404, "not_found", "Not captured yet")
    return data


@router.get("/activities/{activity_type}/{name}", include_in_schema=False)
def plugin_asset(activity_type: str, name: str) -> Response:
    """An activity plug-in's stage renderer (``stage.js``) and style (``stage.css``)."""
    if name not in PLUGIN_ASSETS or activity_type not in editors():
        raise AppError(404, "not_found", "No such plug-in file")
    path = ACTIVITIES_DIR / activity_type / name
    if not path.is_file():
        raise AppError(404, "not_found", "No such plug-in file")
    return FileResponse(path, media_type=PLUGIN_ASSETS[name], headers={"Cache-Control": "no-cache"})


@router.get("/api/live/theme.css", include_in_schema=False)
def session_theme(request: Request) -> Response:
    """The live session's stage font and its own ``theme.css`` (empty when it has neither)."""
    hub = _hub(request)
    sid, folder = hub.session_id, hub.folder
    css = ""
    if sid is not None and folder is not None:
        try:
            css = theme_css(request.app.state.store.load(sid), folder, f"/api/sessions/{sid}/font")
        except SessionError as exc:
            logger.warning("⚠️ live theme without the session's font: %s", exc)
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
                except DomainError as exc:  # LiveError, MusicError, …: refused, the socket stays up
                    client.push({"type": "error", "code": exc.code, "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.disconnect(client)
