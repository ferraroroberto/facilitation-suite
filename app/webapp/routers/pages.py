"""The HTML pages, liveness and build identity."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SCHEMA_VERSION = 1
SPRITE_MARKER = "<!--SPRITE-->"

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache"}


def _page(name: str) -> HTMLResponse:
    """The page with the Lucide sprite inlined at its marker (read per request:
    the pages are small and a restart-free edit shows up on reload)."""
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    if SPRITE_MARKER in html:
        html = html.replace(SPRITE_MARKER, (STATIC_DIR / "sprite.html").read_text(encoding="utf-8"), 1)
    return HTMLResponse(html, headers=_NO_CACHE)


@router.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return _page("index.html")


@router.get("/presenter", include_in_schema=False)
def presenter() -> HTMLResponse:
    return _page("presenter.html")


@router.get("/stage", include_in_schema=False)
def stage() -> HTMLResponse:
    return _page("stage.html")


@router.get("/remote", include_in_schema=False)
def remote() -> HTMLResponse:
    """The phone remote. The page itself is open (an unpaired phone is told how
    to pair); everything it loads needs the token (``app/webapp/auth.py``)."""
    return _page("remote.html")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/version")
def version(request: Request) -> dict[str, object]:
    build = request.app.state.build
    return {
        "app": "facilitation-suite",
        "git_sha": build["git_sha"],
        "captured_at": build["captured_at"],
        "schema_version": SCHEMA_VERSION,
    }
