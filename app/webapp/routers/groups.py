"""Groups: the roster, presence, the three breakout rounds, Zoom exports."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.groups import roster as rs
from src.sessions.model import Item, new_id
from src.sessions.store import SessionError, SessionStore, atomic_write_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions/{sid}")

Round = Literal["pairs", "g4a", "g4b"]


class RosterBody(BaseModel):
    path: str = Field(min_length=1)


class PresenceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    present: bool


class ShuffleBody(BaseModel):
    seed: Optional[int] = None


class RevealBody(BaseModel):
    round: Round = "pairs"


def _store(request: Request) -> SessionStore:
    return request.app.state.store


def _folder(request: Request, sid: str) -> Path:
    try:
        return _store(request).folder(sid)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


def _call(fn, *args):  # noqa: ANN001, ANN202 — thin error translation
    try:
        return fn(*args)
    except rs.RosterError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


def _changed(request: Request, sid: str) -> None:
    """Rounds feed the live "who are you with?" reveal: reload a live session."""
    hub = getattr(request.app.state, "live", None)
    if hub is not None:
        hub.session_saved(sid)


@router.get("/groups")
def get_groups(request: Request, sid: str) -> dict[str, Any]:
    return _call(rs.payload, _folder(request, sid))


@router.post("/roster")
def import_roster(request: Request, sid: str, body: RosterBody) -> dict[str, Any]:
    folder = _folder(request, sid)
    _call(rs.import_roster, folder, Path(body.path.strip().strip('"')))
    return _call(rs.payload, folder)


@router.put("/groups/presence")
def presence(request: Request, sid: str, body: PresenceBody) -> dict[str, Any]:
    folder = _folder(request, sid)
    _call(rs.set_present, folder, body.name, body.present)
    return _call(rs.payload, folder)


@router.post("/groups/shuffle")
async def shuffle(request: Request, sid: str, body: ShuffleBody) -> dict[str, Any]:
    folder = _folder(request, sid)
    try:
        await asyncio.to_thread(rs.shuffle, folder, body.seed)  # up to 120 000 tries: off the event loop
    except rs.RosterError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc
    _changed(request, sid)
    return _call(rs.payload, folder)


@router.get("/groups/zoom.csv")
def zoom_csv(request: Request, sid: str, round: Round = "pairs") -> Response:  # noqa: A002 — the API's word
    folder = _folder(request, sid)
    people, state = _call(rs.roster_state, folder)
    if not state.get("rounds"):
        raise AppError(409, "not_shuffled", "Shuffle the groups first")
    text, missing = rs.zoom_csv(people, state["rounds"][round])
    if text is None:
        raise AppError(409, "missing_emails", f"{len(missing)} present participants have no email", missing)
    try:
        atomic_write_text(folder / "exports" / f"zoom-rooms-{round}.csv", text)
    except OSError as exc:
        logger.warning("⚠️ could not keep a copy of the Zoom CSV in exports/: %s", exc)
    return Response(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="zoom-rooms-{round}.csv"'})


@router.post("/groups/reveal")
def add_reveal(request: Request, sid: str, body: RevealBody) -> dict[str, Any]:
    """Add a "who are you with?" item for a round at the end of the first section."""
    st = _store(request)
    try:
        session = st.load(sid)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc
    if not session.sections:
        raise AppError(409, "no_sections", "The plan has no sections yet — import the slides first")
    item = Item(kind="activity", id=new_id("act"), type="groups_reveal", title="Who are you with?",
                profile="screen_only", options={"round": body.round})
    session.sections[0].items.append(item)
    try:
        st.save(sid, session)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc
    _changed(request, sid)
    return {"item_id": item.id, "section": session.sections[0].name}
