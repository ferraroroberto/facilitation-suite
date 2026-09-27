"""The Stream Deck surface — ``POST /api/actions/{action_id}[/{arg}]`` (epic §12).

The same contract as home-automation's generalized action alias
(home-automation#641): one stable URL per button, the id looked up in one
registry (``src/live/actions.py`` — the very intents the keyboard and the
presenter send), ``{"action_id", "ok": true}`` back. A caller names itself in
``X-Automation-Source`` (the fleet Stream Deck plugin sends it).

Callers on this PC need no token. Anyone else is refused until the phone
remote (step 14) brings a bearer token.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Request

from app.webapp.errors import AppError, require_local
from src.live.actions import catalog, run_action
from src.live.hub import LiveError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/actions")

SOURCE_HEADER = "x-automation-source"


def _source(request: Request) -> str:
    return ((request.headers.get(SOURCE_HEADER) or "").strip().lower() or "external")[:32]


async def _run(request: Request, action_id: str, arg: Optional[str]) -> dict[str, Any]:
    require_local(request, "Actions from another device need the remote token (not enabled)")
    source = _source(request)
    hub = request.app.state.live
    try:
        run_action(hub, action_id, arg)
    except LiveError as exc:
        logger.info("ℹ️ action %s%s from %s refused: %s", action_id, f"/{arg}" if arg else "", source, exc)
        raise AppError(exc.status, exc.code, str(exc)) from exc
    request.app.state.last_action = {"action": action_id, "arg": arg, "source": source, "at": int(time.time() * 1000)}
    hub.push_state()
    return {"action_id": action_id, "ok": True}


@router.get("")
def list_actions() -> dict[str, Any]:
    """Every action with the path to put on a button."""
    return {"actions": [dict(a, path=f"/api/actions/{a['id']}" + (f"/{{{a['arg']}}}" if a["arg"] else "")) for a in catalog()]}


@router.post("/{action_id}")
async def post_action(request: Request, action_id: str) -> dict[str, Any]:
    return await _run(request, action_id, None)


@router.post("/{action_id}/{arg}")
async def post_action_arg(request: Request, action_id: str, arg: str) -> dict[str, Any]:
    return await _run(request, action_id, arg)
