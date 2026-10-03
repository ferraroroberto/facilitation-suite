"""Response compression: the entry document, JS/CSS and JSON go out gzipped (#166, perf-review)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
from starlette.routing import Route

from app.webapp.server import GZIP_EXCLUDED_TYPES, GZIP_MIN_BYTES

GZIP = {"Accept-Encoding": "gzip"}


def test_entry_document_is_gzipped(client: TestClient) -> None:
    r = client.get("/", headers=GZIP)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "<html" in r.text  # the client inflated it back to the page


def test_other_pages_and_static_assets_are_gzipped(client: TestClient) -> None:
    for path in ("/presenter", "/static/css/tokens.css"):
        r = client.get(path, headers=GZIP)
        assert r.status_code == 200, path
        assert r.headers.get("content-encoding") == "gzip", path


def test_no_gzip_without_accept_encoding(client: TestClient) -> None:
    r = client.get("/", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers


def test_tiny_responses_stay_uncompressed(client: TestClient) -> None:
    # The liveness probe is a few bytes: gzip framing would cost more than it saves.
    r = client.get("/healthz", headers=GZIP)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


def test_already_compressed_downloads_are_not_recompressed() -> None:
    body = b"x" * (GZIP_MIN_BYTES * 20)

    def make(media_type: str) -> Starlette:
        return Starlette(
            routes=[Route("/f", lambda request: Response(body, media_type=media_type))],
            middleware=[Middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES, exclude_content_types=GZIP_EXCLUDED_TYPES)],
        )

    for media_type in ("application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        r = TestClient(make(media_type)).get("/f", headers=GZIP)
        assert "content-encoding" not in r.headers, media_type
    # The control: a type that is not excluded does get compressed.
    assert TestClient(make("text/plain")).get("/f", headers=GZIP).headers.get("content-encoding") == "gzip"
