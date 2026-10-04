"""Serving the hash-stamped static assets (#209): cached for good under their own URL, never stale.

``.js``/``.css`` go out with their module imports stamped (``src/static_versioning.py``). A request whose
``?v=`` is this process's fleet hash names exactly these bytes, so it is ``immutable`` for a year; any
other URL for the same file (unstamped, or a stale hash from an old page) is ``no-cache`` + an ETag, so it
can only ever revalidate. The pages themselves stay ``no-cache`` (``routers/pages.py``): a cached page
pointing at an old hash would defeat the whole scheme. Icons, fonts and the manifest change rarely and are
cached for a day.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from src.static_versioning import AssetVersions

LONG_CACHE = "public, max-age=31536000, immutable"
DAY_CACHE = "public, max-age=86400"
REVALIDATE = "no-cache"

MEDIA_TYPES = {".js": "text/javascript", ".css": "text/css"}
DAY_SUFFIXES = {".png", ".ico", ".svg", ".ttf", ".woff2", ".webmanifest"}


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def versioned_response(versions: AssetVersions, url_path: str, source: str, scope: Scope, status_code: int = 200) -> Response:
    """A ``.js``/``.css`` body (JS with its imports stamped) with the cache policy above and a strong ETag."""
    suffix = _suffix(url_path)
    body = versions.stamp_js(source, url_path) if suffix == ".js" else source
    etag = f'"{hashlib.sha256(body.encode("utf-8")).hexdigest()[:20]}"'
    headers = {
        "Cache-Control": LONG_CACHE if versions.is_current(scope.get("query_string", b"")) else REVALIDATE,
        "ETag": etag,
    }
    sent = Headers(scope=scope).get("if-none-match", "")
    if sent.strip() == "*" or etag in [tag.strip().removeprefix("W/") for tag in sent.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(body, status_code=status_code, media_type=MEDIA_TYPES[suffix], headers=headers)


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` for one URL prefix: stamped, long-cached ``.js``/``.css``; day-cached icons and fonts;
    everything else revalidates."""

    def __init__(self, *, directory: str, prefix: str, versions: AssetVersions) -> None:
        super().__init__(directory=directory)
        self.prefix = prefix.rstrip("/")
        self.versions = versions

    def file_response(self, full_path: os.PathLike[str], stat_result: os.stat_result, scope: Scope, status_code: int = 200) -> Response:
        suffix = _suffix(str(full_path))
        if suffix in MEDIA_TYPES:
            url_path = f"{self.prefix}/{Path(os.path.relpath(full_path, self.directory)).as_posix()}"
            try:
                source = Path(full_path).read_text(encoding="utf-8")
            except OSError:
                return super().file_response(full_path, stat_result, scope, status_code)
            return versioned_response(self.versions, url_path, source, scope, status_code)
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = DAY_CACHE if suffix in DAY_SUFFIXES else REVALIDATE
        return response
