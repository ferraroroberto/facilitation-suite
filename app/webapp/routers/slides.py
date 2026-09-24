"""PowerPoint import jobs, the re-import review, the imported slides, and the native file picker."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.webapp.errors import AppError, require_local
from src.importer import review as rv
from src.importer.service import INCOMING, Importer, ImportError_
from src.sessions.model import Session, Source, ensure_ids
from src.sessions.store import SessionError, SessionStore, atomic_write_text

logger = logging.getLogger(__name__)
router = APIRouter()

SLIDE_FILE = re.compile(r"^slide-\d+\.png$")


def _store(request: Request) -> SessionStore:
    return request.app.state.store


def _importer(request: Request) -> Importer:
    return request.app.state.importer


def _folder(request: Request, sid: str) -> Path:
    try:
        return _store(request).folder(sid)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


class ImportRequest(BaseModel):
    pptx: str = Field(min_length=1)


@router.post("/api/sessions/{sid}/import", status_code=202)
def start_import(request: Request, sid: str, body: ImportRequest) -> dict[str, Any]:
    _folder(request, sid)
    try:
        job = _importer(request).start(sid, body.pptx)
    except ImportError_ as exc:
        status = 409 if exc.code == "busy" else 422
        raise AppError(status, exc.code, str(exc)) from exc
    return job.as_dict()


@router.get("/api/imports/{job_id}")
def import_status(request: Request, job_id: str) -> dict[str, Any]:
    job = _importer(request).get(job_id)
    if job is None:
        raise AppError(404, "job_not_found", "No such import")
    return job.as_dict()


@router.get("/api/sessions/{sid}/slides")
def slides(request: Request, sid: str) -> dict[str, Any]:
    path = _folder(request, sid) / "slides" / "slides.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": "", "imported_at": None, "sections": [], "slides": []}
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("❌ slides.json unreadable: %s", exc)
        raise AppError(422, "slides_unreadable", "slides.json could not be read") from exc


@router.get("/api/sessions/{sid}/slides/{name}")
def slide_png(request: Request, sid: str, name: str) -> FileResponse:
    if not SLIDE_FILE.match(name):
        raise AppError(404, "not_found", "No such slide")
    path = _folder(request, sid) / "slides" / name
    if not path.is_file():
        raise AppError(404, "not_found", "No such slide")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


class ApplyRequest(BaseModel):
    accepted: list[str] = Field(default_factory=list, max_length=5000)


def _session(request: Request, sid: str) -> Session:
    try:
        return _store(request).load(sid)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


@router.get("/api/sessions/{sid}/reimport")
def reimport_review(request: Request, sid: str) -> dict[str, Any]:
    """The staged re-import compared with the slides and the plan (``pending: false`` when none waits)."""
    folder = _folder(request, sid)
    new_meta = rv.pending(folder)
    if new_meta is None:
        return {"pending": False}
    old_meta = rv.read_meta(folder / "slides" / "slides.json")
    return {"pending": True, **rv.diff(old_meta, new_meta, _session(request, sid))}


@router.get("/api/sessions/{sid}/reimport/slides/{name}")
def reimport_png(request: Request, sid: str, name: str) -> FileResponse:
    if not SLIDE_FILE.match(name):
        raise AppError(404, "not_found", "No such slide")
    path = _folder(request, sid) / "slides" / INCOMING / name
    if not path.is_file():
        raise AppError(404, "not_found", "No such slide")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/api/sessions/{sid}/reimport/apply")
def reimport_apply(request: Request, sid: str, body: ApplyRequest) -> dict[str, Any]:
    folder = _folder(request, sid)
    session = _session(request, sid)
    try:
        result = rv.apply(folder, session, set(body.accepted))
    except rv.ReviewError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc
    meta = result.pop("meta")
    atomic_write_text(folder / "slides" / "slides.json", json.dumps(meta, ensure_ascii=False, indent=1))
    session.source = Source(pptx=meta.get("source", ""), imported_at=datetime.now().replace(microsecond=0))
    try:
        _store(request).save(sid, ensure_ids(session))
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc
    rv.discard(folder)
    hub = getattr(request.app.state, "live", None)
    if hub is not None:
        hub.session_saved(sid)
    logger.info("✅ re-import applied to %s: %s", sid, result)
    return result


@router.delete("/api/sessions/{sid}/reimport")
def reimport_discard(request: Request, sid: str) -> dict[str, bool]:
    rv.discard(_folder(request, sid))
    return {"pending": False}


class PickRequest(BaseModel):
    kind: str = Field(pattern="^(pptx|xlsx|folder|zoom_chat)$")


_pick_lock = threading.Lock()


def zoom_folder() -> Path:
    """Where Zoom saves meeting chats: ``Documents/Zoom`` (the real Documents, even when OneDrive moved it)."""
    docs = Path.home() / "Documents"
    try:
        from win32com.shell import shell, shellcon  # type: ignore[import-not-found]

        docs = Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Documents))
    except Exception as exc:  # noqa: BLE001 — not Windows, or no pywin32: the plain default
        logger.info("ℹ️ Documents folder from the default path (%s)", exc)
    zoom = docs / "Zoom"
    return zoom if zoom.is_dir() else docs


def native_pick(kind: str) -> str:
    """Open a native Windows open-file / folder dialog on this PC and return the choice."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            return filedialog.askdirectory(parent=root, title="Choose the session folder") or ""
        if kind == "zoom_chat":
            return filedialog.askopenfilename(parent=root, title="Choose the chat Zoom saved", initialdir=str(zoom_folder()),
                                              filetypes=[("Zoom saved chat", "*.txt"), ("All files", "*.*")]) or ""
        types = {"pptx": [("PowerPoint", "*.pptx *.pptm *.ppt")], "xlsx": [("Excel", "*.xlsx")]}[kind]
        return filedialog.askopenfilename(parent=root, title="Choose a file", filetypes=types) or ""
    finally:
        root.destroy()


@router.post("/api/pick")
def pick(request: Request, body: PickRequest) -> dict[str, str]:
    require_local(request, "The file picker only opens on this PC")
    if not _pick_lock.acquire(blocking=False):
        raise AppError(409, "busy", "A file dialog is already open")
    try:
        return {"path": native_pick(body.kind)}
    finally:
        _pick_lock.release()
