"""Results → Finish session: rebuild, the PDFs, the Excel report and the analysis in one tap.

Each step reuses the route or module the Results tab's own card calls, in the order a
facilitator runs them by hand, and reports its own status. A rebuild that fails stops
the steps after it, since they would carry the results it did not replace; an export
that fails does not stop the other one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request

from app.webapp.routers import results
from src.activities import rebuild
from src.errors import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions/{sid}")

# a step whose precondition is not met is skipped, not failed: the facilitator has nothing to fix
SKIP_CODES = {"nothing_to_export", "nothing_to_rebuild", "nothing_captured", "no_hub", "running"}


def _step(key: str, status: str, message: str) -> dict[str, str]:
    return {"key": key, "status": status, "message": message}


def _rebuild(request: Request, sid: str) -> dict[str, str]:
    """Rebuild only when something would change, so a wrap-up leaves no empty backup behind."""
    if request.app.state.live.session_id == sid:
        return _step("rebuild", "skipped", "The session is live: close it to rebuild its results")
    folder = request.app.state.store.folder(sid)
    preview = rebuild.preview(folder)
    if not preview["activities"]:
        return _step("rebuild", "skipped", "No capture was frozen")
    if not preview["changed"]:
        return _step("rebuild", "done", "Nothing to change")
    done = rebuild.apply(folder)
    return _step("rebuild", "done", f"{done['changed']} rebuilt")


async def _pdf(request: Request, sid: str) -> dict[str, str]:
    r = await results.export_pdf(request, sid)
    return _step("pdf", "done", f"{r['pages']} pages" if r["pages"] else "Saved")


def _xlsx(request: Request, sid: str) -> dict[str, str]:
    results.export_xlsx(request, sid)
    return _step("xlsx", "done", "Saved")


def _analysis(request: Request, sid: str) -> dict[str, str]:
    results.run_analysis(request, sid)
    return _step("analysis", "started", "Running through the hub")


async def _run(key: str, call: Callable[[], Awaitable[dict[str, str]]]) -> dict[str, str]:
    try:
        return await call()
    except DomainError as exc:
        return _step(key, "skipped" if exc.code in SKIP_CODES else "failed", str(exc))


@router.post("/wrap-up")
async def wrap_up(request: Request, sid: str) -> dict[str, Any]:
    request.app.state.store.load(sid)  # an unknown session is a 404, not four failed steps
    steps = [await _run("rebuild", lambda: asyncio.to_thread(_rebuild, request, sid))]
    if steps[0]["status"] == "failed":
        steps += [_step(k, "skipped", "The rebuild failed") for k in ("pdf", "xlsx", "analysis")]
    else:
        steps.append(await _run("pdf", lambda: _pdf(request, sid)))
        steps.append(await _run("xlsx", lambda: asyncio.to_thread(_xlsx, request, sid)))
        steps.append(await _run("analysis", lambda: asyncio.to_thread(_analysis, request, sid)))
    logger.info("✅ wrap-up of %s: %s", sid, ", ".join(f"{s['key']} {s['status']}" for s in steps))
    return {"steps": steps}
