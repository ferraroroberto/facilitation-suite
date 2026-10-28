"""Results → Rebuild results: preview, apply and undo (``src/activities/rebuild.py``).

A past session's frozen captures recomputed with today's rules. Only a session
that is not live can be rebuilt: the live capture owns ``live/captures/``
while it runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.webapp.errors import AppError
from src.activities import rebuild
from src.sessions.model import Session

router = APIRouter(prefix="/api/sessions/{sid}/rebuild")


def _closed(request: Request, sid: str) -> tuple[Path, Session]:
    store = request.app.state.store
    session = store.load(sid)
    if request.app.state.live.session_id == sid:
        raise AppError(409, "session_live", "This session is live: close it before rebuilding its results")
    return store.folder(sid), session


def _with_rules(summary: dict[str, Any], session: Session) -> dict[str, Any]:
    return {**summary, "stop_capture_on_leave": session.stop_capture_on_leave}


@router.get("")
def preview(request: Request, sid: str) -> dict[str, Any]:
    folder, session = _closed(request, sid)
    return _with_rules(rebuild.preview(folder), session)


@router.post("")
def apply(request: Request, sid: str) -> dict[str, Any]:
    folder, session = _closed(request, sid)
    return _with_rules(rebuild.apply(folder), session)


@router.post("/undo")
def undo(request: Request, sid: str) -> dict[str, Any]:
    folder, _ = _closed(request, sid)
    return rebuild.undo(folder)
