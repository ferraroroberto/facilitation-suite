"""Feedback forms on the main app: the presenter's backstage results, open/close, the CSV,
the stage's QR code and the Plan tab's forms from other sessions.

Behind ``RemoteAuth`` like every other ``/api/*`` route; the public player app
(``app/webapp/player/``) serves only the phone's form page and its submit.

    GET  /api/forms/{item}/results  → the backstage view (``FormsService.results``)
    POST /api/forms/{item}/open     → open the form (its link takes responses)
    POST /api/forms/{item}/close    → close it (its link says "this form is closed")
    GET  /api/forms/{item}/csv      → the responses as CSV, one row per submission
    GET  /api/forms/qr.svg?item=    → the QR of the open form's public link
    GET  /api/forms/library         → every form in the ledger's sessions, to copy into this one
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.webapp.route_pools import SESSION_WORK, run_on
from src.forms.model import FORM_TYPE, FormError, questions
from src.quiz.qr import qr_svg
from src.sessions.store import SessionError, SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forms")


@router.get("/library")
async def library(request: Request) -> dict[str, Any]:
    """Each session's forms (title, intro, questions), newest session first, for the builder's "Copy from"."""
    return {"forms": await run_on(SESSION_WORK, _library, request.app.state.store)}


def _library(store: SessionStore) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in reversed(store.entries()):
        try:
            session = store.load(entry.id)
        except (SessionError, OSError, ValueError) as exc:
            logger.warning("⚠️ forms library: session %s skipped (%s)", entry.id, exc)
            continue
        for it in session.all_items():
            if it.kind == "activity" and it.type == FORM_TYPE:
                out.append({"session_id": entry.id, "session_title": session.title, "item_id": it.id,
                            "title": it.title, "intro": str((it.options or {}).get("intro") or ""),
                            "questions": questions(it.options)})
    return out


@router.get("/qr.svg", include_in_schema=False)
async def form_qr(request: Request, item: str = "") -> Response:
    forms = request.app.state.forms
    url = forms.url(item) if item in forms.open_ids() else None
    if not url:
        raise FormError(404, "closed", "That form is not open, or the public URL is not configured")
    return Response(qr_svg(url), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/{item_id}/results")
async def form_results(request: Request, item_id: str) -> dict[str, Any]:
    return request.app.state.forms.results(item_id)


@router.post("/{item_id}/open")
async def form_open(request: Request, item_id: str) -> dict[str, Any]:
    request.app.state.forms.open(item_id)
    return request.app.state.forms.results(item_id)


@router.post("/{item_id}/close")
async def form_close(request: Request, item_id: str) -> dict[str, Any]:
    request.app.state.forms.close(item_id)
    return request.app.state.forms.results(item_id)


@router.get("/{item_id}/csv")
async def form_csv(request: Request, item_id: str) -> Response:
    text = request.app.state.forms.csv(item_id)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{item_id}.csv"', "Cache-Control": "no-store"})
