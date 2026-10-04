"""The HTML pages, liveness and build identity."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SCHEMA_VERSION = 1
SPRITE_MARKER = "<!--SPRITE-->"

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache"}


def _etag(html: str) -> str:
    """Weak validator for a page: a digest of the exact bytes served. Weak because the bytes on the
    wire vary with ``Content-Encoding``. The asset hashes are stamped into the body, so the body alone
    decides whether a 304 is still true: an edited asset moves its URL and with it the validator."""
    return f'W/"{hashlib.sha256(html.encode("utf-8")).hexdigest()[:20]}"'


def _if_none_match_hits(header: str, etag: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` (a list, ``*``, weak or strong form) with ``etag``."""
    header = header.strip()
    if not header:
        return False
    if header == "*":
        return True
    ours = etag.removeprefix("W/")
    return any(candidate.strip().removeprefix("W/") == ours for candidate in header.split(","))


def _page(request: Request, name: str) -> Response:
    """The page with the Lucide sprite inlined at its marker and every ``.css``/``.js`` URL stamped with the
    fleet hash (read per request: the pages are small). ``no-cache`` + an ETag: a relaunch revalidates and
    gets a bodyless 304 when nothing changed — and a page that did change points at the new asset URLs."""
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    if SPRITE_MARKER in html:
        html = html.replace(SPRITE_MARKER, (STATIC_DIR / "sprite.html").read_text(encoding="utf-8"), 1)
    html = request.app.state.assets.stamp_html(html)
    headers = {**_NO_CACHE, "ETag": _etag(html)}
    if _if_none_match_hits(request.headers.get("if-none-match", ""), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)


@router.get("/", include_in_schema=False)
def index(request: Request) -> Response:
    return _page(request, "index.html")


@router.get("/presenter", include_in_schema=False)
def presenter(request: Request) -> Response:
    return _page(request, "presenter.html")


@router.get("/stage", include_in_schema=False)
def stage(request: Request) -> Response:
    return _page(request, "stage.html")


@router.get("/remote", include_in_schema=False)
def remote(request: Request) -> Response:
    """The phone remote. The page itself is open (an unpaired phone is told how
    to pair); everything it loads needs the token (``app/webapp/auth.py``)."""
    return _page(request, "remote.html")


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
