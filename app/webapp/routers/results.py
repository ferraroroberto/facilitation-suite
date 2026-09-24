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
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.config import data_dir
from src.no_window import NO_WINDOW
from src.results.collect import load_results, participation, read_jsonl
from src.results.excel import build_report
from src.results.pdf import session_html
from src.results.reconcile import reconcile_file
from src.sessions.store import SessionError, SessionStore, atomic_write_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions/{sid}")

ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_FILE, XLSX_FILE, RECONCILE_FILE = "session.pdf", "report.xlsx", "zoom-reconciliation.json"
PDF_TIMEOUT_S = 300


class ReconcileBody(BaseModel):
    path: str = Field(min_length=1)


def _store(request: Request) -> SessionStore:
    return request.app.state.store


def _load(request: Request, sid: str) -> tuple[Path, Any]:
    try:
        return _store(request).folder(sid), _store(request).load(sid)
    except SessionError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


def _file_info(path: Path) -> Optional[dict[str, Any]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"file": path.name, "modified_ms": int(st.st_mtime * 1000), "bytes": st.st_size}


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
    data["exports"] = {"pdf": _file_info(folder / "exports" / PDF_FILE), "xlsx": _file_info(folder / "exports" / XLSX_FILE)}
    return data


@router.get("/results/captures/{item_id}.png")
def capture_png(request: Request, sid: str, item_id: str) -> FileResponse:
    folder, _ = _load(request, sid)
    path = folder / "live" / "captures" / f"{item_id}.png"
    if not ITEM_ID.match(item_id) or not path.is_file():
        raise AppError(404, "not_found", "No frozen image for that activity")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


def _print(html: str, out: Path) -> int:
    """Write the print document to a scratch file and print it in its own process."""
    scratch = data_dir() / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=scratch, delete=False, encoding="utf-8") as fh:
        fh.write(html)
        doc = Path(fh.name)
    try:
        proc = subprocess.run([sys.executable, "-m", "src.results.pdf", str(doc), str(out)], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, timeout=PDF_TIMEOUT_S, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise AppError(504, "pdf_timeout", f"The PDF took longer than {PDF_TIMEOUT_S} s — see the log") from exc
    finally:
        doc.unlink(missing_ok=True)
    if proc.returncode != 0:
        logger.error("❌ session PDF process exited %s: %s", proc.returncode, (proc.stderr or "").strip()[-500:])
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
    pages = await asyncio.to_thread(_print, session_html(folder, data), out)
    missing = [a["title"] for a in data["activities"] if not a["has_png"]]
    logger.info("✅ session PDF for %s: %d slides + %d live results, %d pages", sid, data["slides"], data["captures"], pages)
    return {"file": PDF_FILE, "pages": pages or None, "slides": data["slides"], "captures": data["captures"],
            "missing_images": missing, **(_file_info(out) or {})}


@router.get("/exports/session.pdf")
def download_pdf(request: Request, sid: str) -> FileResponse:
    folder, _ = _load(request, sid)
    path = folder / "exports" / PDF_FILE
    if not path.is_file():
        raise AppError(404, "not_built", "Export the session PDF first")
    return FileResponse(path, media_type="application/pdf", filename=PDF_FILE)


@router.get("/exports/report.xlsx")
def download_xlsx(request: Request, sid: str) -> FileResponse:
    folder, session = _load(request, sid)
    data = load_results(folder, session)
    if not data["activities"]:
        raise AppError(409, "nothing_to_export", "No activity was captured in this session yet")
    people = participation(data["activities"], read_jsonl(folder / "live" / "chat.jsonl"))
    out = folder / "exports" / XLSX_FILE
    try:
        build_report(data, people, out)
    except OSError as exc:
        raise AppError(500, "write_failed", f"Could not write {XLSX_FILE}: {exc}") from exc
    logger.info("✅ Excel report for %s: %d activities, %d people", sid, len(data["activities"]), len(people))
    return FileResponse(out, filename=XLSX_FILE,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
