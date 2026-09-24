"""PowerPoint import jobs, the imported slides, and the native file picker."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.webapp.errors import AppError, require_local
from src.importer.service import Importer, ImportError_
from src.sessions.store import SessionError, SessionStore

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
