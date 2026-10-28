"""Results: per-activity answers, the session PDF, the Excel report, the Zoom check.

Everything reads the session folder (``src/results``), so a finished session
works without being live. Exports land in the folder's ``exports/`` (epic
§5.1) and are served from there; the reconciliation report is kept as
``exports/zoom-reconciliation.json`` so the tab shows it next time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.activities.rebuild import backups
from src.config import data_dir
from src.errors import DomainError
from src.groups.roster import roster_state
from src.jsonl import read_jsonl
from src.logger import relay
from src.no_window import NO_WINDOW
from src.results.aliases import Excluded, read_aliases, seen_names, suggest, write_aliases
from src.results.analysis import analysis_facts
from src.results.collect import load_results, participation
from src.results.excel import build_report
from src.results.pdf import activities_html, activities_pages, session_html, share_html, share_pages
from src.results.reconcile import reconcile_file
from src.sessions.model import ITEM_ID, Session
from src.sessions.store import SessionStore, atomic_write_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions/{sid}")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_FILE, SHARE_FILE, ACTIVITIES_FILE = "session-full.pdf", "session-share.pdf", "session-activities.pdf"
LEGACY_PDF_FILE = "session.pdf"  # what the full PDF was called before the share copy existed
XLSX_FILE, RECONCILE_FILE = "report.xlsx", "zoom-reconciliation.json"
PDF_TIMEOUT_S = 300


class ReconcileBody(BaseModel):
    path: str = Field(min_length=1)


class MergeBody(BaseModel):
    canonical: str = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)


class UnmergeBody(BaseModel):
    name: str = Field(min_length=1)


def _store(request: Request) -> SessionStore:
    return request.app.state.store


def _load(request: Request, sid: str) -> tuple[Path, Any]:
    return _store(request).folder(sid), _store(request).load(sid)


def _file_info(path: Path) -> Optional[dict[str, Any]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"file": path.name, "modified_ms": int(st.st_mtime * 1000), "bytes": st.st_size}


def _full_pdf(folder: Path) -> Path:
    """The facilitator's PDF; an older session folder only has the file under its former name until it is exported again."""
    full = folder / "exports" / PDF_FILE
    legacy = folder / "exports" / LEGACY_PDF_FILE
    return legacy if not full.is_file() and legacy.is_file() else full


def _reconciliation(folder: Path) -> Optional[dict[str, Any]]:
    path = folder / "exports" / RECONCILE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ %s unreadable (%s)", RECONCILE_FILE, exc)
        return None


@router.get("/results")
def results(request: Request, sid: str) -> dict[str, Any]:
    folder, session = _load(request, sid)
    data = load_results(folder, session)
    data["reconciliation"] = _reconciliation(folder)
    data["exports"] = {"pdf": _file_info(_full_pdf(folder)), "share": _file_info(folder / "exports" / SHARE_FILE),
                       "activities": _file_info(folder / "exports" / ACTIVITIES_FILE), "xlsx": _file_info(folder / "exports" / XLSX_FILE)}
    data["people"] = _people(folder, session, data)
    data["rebuild_backup"] = (backups(folder) or [None])[-1]
    return data


@router.get("/analysis")
def analysis(request: Request, sid: str) -> dict[str, Any]:
    """The *Analyze session* button's state: the command to copy and which inputs the analysis will find."""
    folder, session = _load(request, sid)
    return analysis_facts(folder, len(load_results(folder, session)["activities"]))


def _roster_names(folder: Path) -> list[str]:
    try:
        return [p.name for p in roster_state(folder)[0]]
    except DomainError as exc:
        logger.warning("⚠️ roster unreadable for merge suggestions (%s)", exc)
        return []


def _people(folder: Path, session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """The names seen, the merges in force and the likely ones still to confirm (never applied here)."""
    aliases = read_aliases(folder)
    names = seen_names(data["activities"], read_jsonl(folder / "live" / "chat.jsonl"),
                       Excluded(session.excluded_senders, aliases))
    return {"names": sorted({aliases.canonical(n) for n in names}, key=str.casefold), "merges": aliases.groups,
            "suggestions": suggest(names, aliases, _roster_names(folder))}


@router.post("/aliases/merge")
def merge_names(request: Request, sid: str, body: MergeBody) -> dict[str, Any]:
    folder, _ = _load(request, sid)
    aliases = read_aliases(folder, strict=True)
    aliases.merge(body.canonical, body.aliases)
    write_aliases(folder, aliases)
    logger.info("ℹ️ results: %d name group(s) merged in %s", len(aliases.groups), sid)
    return {"merges": aliases.groups}


@router.post("/aliases/unmerge")
def unmerge_name(request: Request, sid: str, body: UnmergeBody) -> dict[str, Any]:
    folder, _ = _load(request, sid)
    aliases = read_aliases(folder, strict=True)
    aliases.unmerge(body.name)
    write_aliases(folder, aliases)
    return {"merges": aliases.groups}


@router.get("/results/captures/{item_id}.png")
def capture_png(request: Request, sid: str, item_id: str) -> FileResponse:
    folder, _ = _load(request, sid)
    path = folder / "live" / "captures" / f"{item_id}.png"
    if not ITEM_ID.fullmatch(item_id) or not path.is_file():
        raise AppError(404, "not_found", "No frozen image for that activity")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


def _participation(folder: Path, session: Session, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Each person's counts, the same rows for the PDF and the Excel report."""
    aliases = read_aliases(folder)
    return participation(data["activities"], read_jsonl(folder / "live" / "chat.jsonl"), aliases,
                         Excluded(session.excluded_senders, aliases))


def _print(html: str, out: Path) -> int:
    """Write the print document to a scratch file and print it in its own process."""
    scratch = data_dir() / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=scratch, delete=False, encoding="utf-8") as fh:
        fh.write(html)
        doc = Path(fh.name)
    try:
        proc = subprocess.run([sys.executable, "-m", "src.results.pdf", str(doc), str(out)], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=PDF_TIMEOUT_S, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or ""  # str on Windows (read back after the kill), bytes elsewhere
        relay((err.decode("utf-8", "replace") if isinstance(err, bytes) else err).splitlines(), logger)
        raise AppError(504, "pdf_timeout", f"The PDF took longer than {PDF_TIMEOUT_S} s — see the log") from exc
    finally:
        doc.unlink(missing_ok=True)
    relay((proc.stderr or "").splitlines(), logger)  # the helper logs to stderr only
    if proc.returncode != 0:
        logger.error("❌ session PDF process exited %s", proc.returncode)
        raise AppError(500, "pdf_failed", "The session PDF could not be printed — see the log")
    try:
        return int((proc.stdout or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


@router.post("/exports/pdf")
async def export_pdf(request: Request, sid: str) -> dict[str, Any]:
    folder, session = _load(request, sid)
    data = load_results(folder, session)
    if not data["pages"] and not data["activities"]:
        raise AppError(409, "nothing_to_export", "Nothing was shown live in this session yet")
    out = folder / "exports" / PDF_FILE
    pages = await asyncio.to_thread(_print, session_html(folder, data, _participation(folder, session, data)), out)
    # each copy prints in its own process; a copy with no page to print leaves an older one as it was
    share_out = folder / "exports" / SHARE_FILE
    share_count = (await asyncio.to_thread(_print, share_html(folder, data), share_out) if share_pages(data) else None)
    shared_out = folder / "exports" / ACTIVITIES_FILE
    shared_pages = (await asyncio.to_thread(_print, activities_html(folder, data), shared_out)
                    if activities_pages(data) else None)
    missing = [a["title"] for a in data["activities"] if not a["has_png"]]
    logger.info("✅ session PDFs for %s: %d slides + %d live results + %d quiz pages, %d pages full, %s share, %s participants'", sid,
                data["slides"], data["captures"], data["quiz_pages"], pages, share_count, shared_pages)
    return {"file": PDF_FILE, "pages": pages or None, "slides": data["slides"], "captures": data["captures"],
            "quiz_pages": data["quiz_pages"],
            "missing_images": missing, **(_file_info(out) or {}),
            "share": {"file": SHARE_FILE, "pages": share_count or None, **(_file_info(share_out) or {})}
            if share_count is not None else None,
            "activities": {"file": ACTIVITIES_FILE, "pages": shared_pages or None, **(_file_info(shared_out) or {})}
            if shared_pages is not None else None}


@router.post("/exports/xlsx")
def export_xlsx(request: Request, sid: str) -> dict[str, Any]:
    folder, session = _load(request, sid)
    data = load_results(folder, session)
    if not data["activities"] and not data["quizzes"]:
        raise AppError(409, "nothing_to_export", "No activity was captured in this session yet")
    people = _participation(folder, session, data)
    out = folder / "exports" / XLSX_FILE
    try:
        build_report(data, people, out)
    except OSError as exc:
        raise AppError(500, "write_failed", f"Could not write {XLSX_FILE}: {exc}") from exc
    logger.info("✅ Excel report for %s: %d activities, %d quiz games, %d people", sid, len(data["activities"]),
                len(data["quizzes"]), len(people))
    return {"file": XLSX_FILE, **(_file_info(out) or {})}


def _open_export(request: Request, sid: str, name: str, media_type: str, hint: str) -> FileResponse:
    """Serve a built export inline: a tab (or the phone's viewer) opens it, nothing is saved to Downloads."""
    path = _store(request).folder(sid) / "exports" / name
    if not path.is_file():
        raise AppError(404, "not_built", hint)
    # no-transform: the xlsx is a zip, which the compression middleware cannot tell from its content type.
    return FileResponse(path, media_type=media_type, filename=name, content_disposition_type="inline",
                        headers={"Cache-Control": "no-transform"})


@router.get("/exports/session-full.pdf")
@router.get("/exports/session.pdf")  # the former name, kept for links to an older export
def open_pdf(request: Request, sid: str) -> FileResponse:
    return _open_export(request, sid, _full_pdf(_store(request).folder(sid)).name, "application/pdf", "Export the session PDF first")


@router.get("/exports/session-share.pdf")
def open_share_pdf(request: Request, sid: str) -> FileResponse:
    return _open_export(request, sid, SHARE_FILE, "application/pdf", "Export the session PDF first")


@router.get("/exports/session-activities.pdf")
def open_activities_pdf(request: Request, sid: str) -> FileResponse:
    return _open_export(request, sid, ACTIVITIES_FILE, "application/pdf", "Export the session PDF first")


@router.get("/exports/report.xlsx")
def open_xlsx(request: Request, sid: str) -> FileResponse:
    return _open_export(request, sid, XLSX_FILE,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Export the Excel report first")


@router.post("/reconcile")
def reconcile(request: Request, sid: str, body: ReconcileBody) -> dict[str, Any]:
    folder, _ = _load(request, sid)
    path = Path(body.path.strip().strip('"'))
    if not path.is_file():
        raise AppError(404, "not_found", f"No file at {path}")
    try:
        report = reconcile_file(path, read_jsonl(folder / "live" / "chat.jsonl"))
    except OSError as exc:
        raise AppError(422, "unreadable", f"Could not read {path.name}: {exc}") from exc
    if not report["zoom_messages"]:
        raise AppError(422, "not_a_zoom_chat", f"{path.name} has no chat messages Zoom's way — is it the saved chat?")
    try:
        atomic_write_text(folder / "exports" / RECONCILE_FILE, json.dumps(report, ensure_ascii=False, indent=1))
    except OSError as exc:
        logger.warning("⚠️ could not keep the reconciliation in exports/: %s", exc)
    return report
