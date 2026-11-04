"""The after-call transcript from the podcast recorder's takes (``src/copilot/after_call.py``), on the Results tab.

The folder is typed on this PC and read from it, so every write is local-only. Starting a run saves the
folder in ``session.yaml`` first, so the next visit shows the same one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.webapp.errors import require_local
from src.copilot.after_call import AfterCall
from src.sessions.model import RECORDER_FOLDER_CHARS, Session
from src.sessions.store import SessionStore

router = APIRouter(prefix="/api/sessions/{sid}/results/recorded")


class RunBody(BaseModel):
    folder: str = Field(min_length=1, max_length=RECORDER_FOLDER_CHARS)


def _parts(request: Request) -> tuple[SessionStore, AfterCall]:
    return request.app.state.store, request.app.state.after_call


@router.get("")
def recorded_status(request: Request, sid: str) -> dict[str, Any]:
    """The saved folder, the consent, the current or last run, and what the transcript and summary files hold."""
    store, after_call = _parts(request)
    return after_call.status(sid, store.folder(sid), store.load(sid))


@router.post("")
def start_recorded(request: Request, sid: str, body: RunBody) -> dict[str, Any]:
    """Save the recorder folder and transcribe its takes from while the session was live, then summarise them."""
    require_local(request, "The recorder tracks can only be read on this PC")
    store, after_call = _parts(request)

    def keep(session: Session) -> None:
        session.copilot_recorder_folder = body.folder.strip()

    session = store.edit(sid, keep)
    after_call.start(sid, store.folder(sid), session)
    return after_call.status(sid, store.folder(sid), session)


@router.post("/summary")
def summarise_again(request: Request, sid: str) -> dict[str, Any]:
    """Ask again for the summary of the recorded transcript already written."""
    require_local(request, "The summary can only be asked for on this PC")
    store, after_call = _parts(request)
    session = store.load(sid)
    after_call.start_summary(sid, store.folder(sid), session)
    return after_call.status(sid, store.folder(sid), session)
