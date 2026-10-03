"""ETag + 304 on the pages, so a relaunch revalidates without re-downloading the document (#166, perf-review)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers import pages

PAGES = ("/", "/presenter", "/stage", "/remote")


@pytest.mark.parametrize("path", PAGES)
def test_every_page_carries_a_weak_etag_and_still_revalidates(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert re.fullmatch(r'W/"[0-9a-f]{20}"', r.headers.get("etag", ""))
    assert r.headers["cache-control"] == "no-cache"


def test_matching_if_none_match_answers_a_bodyless_304(client: TestClient) -> None:
    etag = client.get("/").headers["etag"]
    r = client.get("/", headers={"If-None-Match": etag, "Accept-Encoding": "gzip"})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag
    assert r.headers["cache-control"] == "no-cache"  # the next launch revalidates again
    assert "content-encoding" not in r.headers  # a bodyless 304 is never gzipped


@pytest.mark.parametrize(
    "wrap",
    [lambda e: e[2:], lambda e: "*", lambda e: f'"zzz", {e}'],
    ids=["strong-form", "star", "list"],
)
def test_if_none_match_variants_answer_304(client: TestClient, wrap) -> None:
    etag = client.get("/stage").headers["etag"]
    assert client.get("/stage", headers={"If-None-Match": wrap(etag)}).status_code == 304


def test_a_stale_validator_gets_the_full_page(client: TestClient) -> None:
    r = client.get("/", headers={"If-None-Match": 'W/"00000000000000000000"'})
    assert r.status_code == 200
    assert "<html" in r.text


def test_an_edited_page_invalidates_the_validator(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = client.get("/").headers["etag"]
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text((pages.STATIC_DIR / "index.html").read_text(encoding="utf-8") + "<!-- edit -->", encoding="utf-8")
    (static / "sprite.html").write_text((pages.STATIC_DIR / "sprite.html").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(pages, "STATIC_DIR", static)
    r = client.get("/", headers={"If-None-Match": old})
    assert r.status_code == 200
    assert r.headers["etag"] != old


def test_an_edited_sprite_invalidates_the_validator(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The sprite is inlined into the page, so editing it changes the bytes served and must move the validator."""
    old = client.get("/").headers["etag"]
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text((pages.STATIC_DIR / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    (static / "sprite.html").write_text((pages.STATIC_DIR / "sprite.html").read_text(encoding="utf-8") + "<!-- icon -->", encoding="utf-8")
    monkeypatch.setattr(pages, "STATIC_DIR", static)
    assert client.get("/", headers={"If-None-Match": old}).status_code == 200
