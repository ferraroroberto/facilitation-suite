"""Response compression: the entry document, JS/CSS and JSON go out gzipped through the vendored middleware."""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from src.compression import StreamSafeGZipMiddleware

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


def test_compression_is_the_outermost_layer_and_the_only_gzip(client: TestClient) -> None:
    stack = [m.cls for m in client.app.user_middleware]
    assert stack[0] is StreamSafeGZipMiddleware  # added last: an auth refusal or a 304 passes it unchanged
    assert GZipMiddleware not in stack


def test_the_live_websocket_still_delivers_its_first_message_at_once(client: TestClient) -> None:
    # The app's only stream is the /ws socket; a gzip client must not hold its first frame back.
    with client.websocket_connect("/ws?role=presenter", headers=GZIP) as ws:
        assert ws.receive_json()["type"] == "plan"
        assert ws.receive_json()["type"] == "state"
