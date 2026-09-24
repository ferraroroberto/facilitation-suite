"""Sessions: the ledger, session folders, session.yaml, the offline check."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError

from app.webapp.errors import AppError
from src.sessions import readiness
from src.sessions.model import dump_session, parse_session
from src.sessions.offline import check_folder, pin_folder
from src.sessions.store import SessionError, SessionStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions")


def store(request: Request) -> SessionStore:
    return request.app.state.store


def _err(exc: SessionError) -> AppError:
    return AppError(exc.status, exc.code, str(exc))


class NewSession(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    workshop: str = Field("", max_length=100)
    folder: str = Field("", max_length=100)
    date: Optional[datetime] = None
    duration_minutes: int = Field(120, ge=1, le=1440)
    root: Optional[str] = None


class AddExisting(BaseModel):
    path: str = Field(min_length=1)


class Duplicate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    folder: str = Field("", max_length=100)
    workshop: Optional[str] = None


class OpenTarget(BaseModel):
    what: str = Field(pattern="^(folder|yaml)$")


@router.get("")
def list_sessions(request: Request) -> dict[str, Any]:
    st = store(request)
    try:
        rows = [st.summary(e) for e in st.entries()]
    except SessionError as exc:
        raise _err(exc) from exc
    return {"sessions": rows, "session_root": str(st.default_root())}


@router.post("", status_code=201)
def create_session(request: Request, body: NewSession) -> dict[str, Any]:
    st = store(request)
    try:
        entry = st.create(body.title, body.workshop, body.folder or body.title, date=body.date,
                          duration_minutes=body.duration_minutes, root=body.root)
    except SessionError as exc:
        raise _err(exc) from exc
    except OSError as exc:
        logger.error("❌ create session failed: %s", exc)
        raise AppError(500, "folder_error", "The session folder could not be created") from exc
    return st.summary(entry)


@router.post("/add", status_code=201)
def add_existing(request: Request, body: AddExisting) -> dict[str, Any]:
    st = store(request)
    try:
        return st.summary(st.add_existing(body.path))
    except SessionError as exc:
        raise _err(exc) from exc


@router.post("/{sid}/duplicate", status_code=201)
def duplicate(request: Request, sid: str, body: Duplicate) -> dict[str, Any]:
    st = store(request)
    try:
        return st.summary(st.duplicate(sid, body.title, body.folder or body.title, body.workshop))
    except SessionError as exc:
        raise _err(exc) from exc


@router.delete("/{sid}")
def remove(request: Request, sid: str) -> dict[str, Any]:
    try:
        store(request).remove(sid)
    except SessionError as exc:
        raise _err(exc) from exc
    return {"removed": sid}


def live_facts(request: Request) -> dict[str, Any]:
    """Facts other services know (reader test, OBS) for the readiness list."""
    facts: dict[str, Any] = {}
    for key in ("reader", "obs"):
        svc = getattr(request.app.state, key, None)
        if svc is not None and hasattr(svc, "readiness"):
            facts[key] = svc.readiness()
    return facts


def session_payload(request: Request, sid: str, *, with_offline: bool = True) -> dict[str, Any]:
    st = store(request)
    try:
        entry = st.entry(sid)
        session = st.load(sid)
    except SessionError as exc:
        raise _err(exc) from exc
    folder = Path(entry.path)
    offline = check_folder(folder)
    meta = readiness.slides_meta(folder) or {}
    return {
        "summary": st.summary(entry),
        "session": dump_session(session),
        "folder": {
            "path": entry.path,
            "crumbs": list(folder.parts[-6:]),
            "slides": len(meta.get("slides") or []),
            "roster": (folder / "roster.xlsx").is_file(),
            "groups": (folder / "groups.yaml").is_file(),
            "offline": offline.as_dict(),
        },
        "readiness": readiness.build(folder, session, offline, live_facts(request)),
    }


@router.get("/{sid}")
def get_session(request: Request, sid: str) -> dict[str, Any]:
    return session_payload(request, sid)


@router.put("/{sid}")
def put_session(request: Request, sid: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("session", body)
    try:
        session = parse_session(raw)
    except (ValueError, ValidationError) as exc:
        raise AppError(422, "invalid_session", "The plan is not valid", str(exc)) from exc
    try:
        store(request).save(sid, session)
    except SessionError as exc:
        raise _err(exc) from exc
    hub = getattr(request.app.state, "live", None)
    if hub is not None:
        hub.session_saved(sid)
    return {"session": dump_session(session)}


@router.get("/{sid}/offline")
def offline(request: Request, sid: str) -> dict[str, Any]:
    try:
        folder = store(request).folder(sid)
    except SessionError as exc:
        raise _err(exc) from exc
    return check_folder(folder).as_dict()


@router.post("/{sid}/pin")
def pin(request: Request, sid: str) -> dict[str, Any]:
    try:
        folder = store(request).folder(sid)
    except SessionError as exc:
        raise _err(exc) from exc
    ok, message = pin_folder(folder)
    if not ok:
        raise AppError(422, "pin_failed", message)
    return {"message": message, "offline": check_folder(folder).as_dict()}


@router.post("/{sid}/open")
def open_in_explorer(request: Request, sid: str, body: OpenTarget) -> dict[str, str]:
    """Open the folder (Explorer) or session.yaml (default editor) on this PC."""
    try:
        folder = store(request).folder(sid)
    except SessionError as exc:
        raise _err(exc) from exc
    target = folder if body.what == "folder" else folder / "session.yaml"
    if sys.platform != "win32":
        raise AppError(501, "unsupported", "Opening files is only available on Windows")
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606 — local shell open
    except OSError as exc:
        logger.warning("⚠️ open %s failed: %s", body.what, exc)
        raise AppError(422, "open_failed", "Windows could not open it") from exc
    return {"opened": body.what}
