"""Hash-stamped static assets are cached for good; the pages revalidate (#209, perf-review).

The warm launch used to revalidate every asset (``no-cache``) and the ES-module imports chained those
round trips serially. Now every ``.js``/``.css`` URL carries the fleet hash (``?v=``), a URL with the
current hash is ``immutable``, and anything else — an unstamped or stale URL — still revalidates, so a
deploy can never leave a device on old code.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient

PAGES = ("/", "/presenter", "/stage", "/remote")
LONG = "public, max-age=31536000, immutable"
ASSET_URL = re.compile(r"""(?:href|src)=["'](/(?:static|themes)/[\w\-./]+\.(?:css|js))(\?[^"']*)?["']""")
IMPORT_SPEC = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*['"]((?:/|\./|\.\./)[^'"]+\.js)(\?[^'"]*)?['"]""")


def _shell_assets(client: TestClient, page: str = "/") -> list[tuple[str, str]]:
    return ASSET_URL.findall(client.get(page).text)


def _fleet_hash(client: TestClient) -> str:
    """The hash the shell stamps (read back from the page, not from the implementation)."""
    query = dict(_shell_assets(client))["/static/css/tokens.css"]
    assert re.fullmatch(r"\?v=[0-9a-f]{8}", query), query
    return query[3:]


@pytest.mark.parametrize("page", PAGES)
def test_every_shell_asset_url_is_stamped_with_one_fleet_hash(client: TestClient, page: str) -> None:
    assets = _shell_assets(client, page)
    assert assets, page
    stamps = {query for _, query in assets}
    assert len(stamps) == 1 and re.fullmatch(r"\?v=[0-9a-f]{8}", stamps.pop()), assets


@pytest.mark.parametrize("page", PAGES)
def test_the_shell_still_revalidates(client: TestClient, page: str) -> None:
    assert client.get(page).headers["cache-control"] == "no-cache"


def test_stamped_assets_are_immutable(client: TestClient) -> None:
    v = _fleet_hash(client)
    for path in ("/static/css/tokens.css", "/static/js/app.js", "/themes/default.css", "/static/_vendored/nav/nav-tabs.js"):
        r = client.get(f"{path}?v={v}")
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == LONG, path


@pytest.mark.parametrize("query", ["", "?v=00000000", "?v="], ids=["unstamped", "stale", "empty"])
def test_an_unstamped_or_stale_url_never_gets_the_long_cache(client: TestClient, query: str) -> None:
    r = client.get(f"/static/js/app.js{query}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_the_stamped_script_revalidates_by_etag_when_it_is_asked_to(client: TestClient) -> None:
    v = _fleet_hash(client)
    first = client.get(f"/static/js/ui.js?v={v}")
    assert first.headers.get("etag")
    again = client.get(f"/static/js/ui.js?v={v}", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_icons_and_fonts_are_cached_for_a_day(client: TestClient) -> None:
    for path in ("/static/icons/icon-192.png", "/static/fonts/PatrickHand-Regular.ttf", "/static/manifest.webmanifest"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "public, max-age=86400", path


def test_every_module_import_in_the_graph_is_stamped(client: TestClient) -> None:
    """Crawl the ES-module graph from each shell (static, relative and dynamic ``import()`` specifiers):
    an import with no hash would be a second, never-revalidated copy of the module."""
    v = _fleet_hash(client)
    todo = [urljoin("http://x", f"{path}{query}") for page in PAGES for path, query in _shell_assets(client, page) if path.endswith(".js")]
    seen: set[str] = set()
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        seen.add(url)
        parts = urlsplit(url)
        body = client.get(parts.path + ("?" + parts.query if parts.query else "")).text
        for spec, query in IMPORT_SPEC.findall(body):
            assert query == f"?v={v}", f"{parts.path} imports {spec}{query!r} without the fleet hash"
            todo.append(urljoin(url, spec) + query)
    assert {urlsplit(u).path for u in seen} >= {
        "/static/js/app.js",
        "/static/js/views/sessions.js",  # a dynamic import() from app.js
        "/static/js/stage-render.js",
        "/static/_vendored/empty-state/empty-state.js",
        "/static/_vendored/icons/icons.js",  # a relative ../icons/icons.js import from the vendored empty-state
    }


def test_the_activity_plugins_are_stamped_and_immutable_too(client: TestClient) -> None:
    v = _fleet_hash(client)
    r = client.get(f"/activities/quiz_lobby/stage.js?v={v}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == LONG
    assert re.search(rf"/activities/quiz/stage\.js\?v={v}['\"]", r.text)  # the plugin it builds on
    assert client.get("/activities/quiz_lobby/stage.js").headers["cache-control"] == "no-cache"
    assert client.get("/activities/map/world.svg").status_code == 200


def test_a_changed_asset_gets_a_new_url(tmp_path: Path) -> None:
    from src.static_versioning import AssetVersions

    static = tmp_path / "static"
    (static / "js").mkdir(parents=True)
    (static / "js" / "a.js").write_text("export const a = 1;\n", encoding="utf-8")
    (static / "js" / "b.js").write_text("import { a } from '/static/js/a.js';\n", encoding="utf-8")
    page = '<script type="module" src="/static/js/b.js"></script>'

    before = AssetVersions({"/static": static})
    old_page = before.stamp_html(page)
    old_b = before.stamp_js(before.read("/static/js/b.js"), "/static/js/b.js")
    assert re.search(r'src="/static/js/b\.js\?v=[0-9a-f]{8}"', old_page)

    # Editing only a.js — b.js's own bytes are untouched — still moves every URL (one fleet hash).
    (static / "js" / "a.js").write_text("export const a = 2;\n", encoding="utf-8")
    after = AssetVersions({"/static": static})
    assert after.fleet_hash != before.fleet_hash
    assert after.stamp_html(page) != old_page
    assert after.stamp_js(after.read("/static/js/b.js"), "/static/js/b.js") != old_b


def test_stamping_is_idempotent_and_leaves_unknown_files_alone(tmp_path: Path) -> None:
    from src.static_versioning import AssetVersions

    static = tmp_path / "static"
    static.mkdir()
    (static / "a.js").write_text("export {};\n", encoding="utf-8")
    versions = AssetVersions({"/static": static})
    src = "import './a.js';\nimport('/static/a.js?v=old');\nimport '/static/missing.js';\n"
    once = versions.stamp_js(src, "/static/main.js")
    assert versions.stamp_js(once, "/static/main.js") == once
    assert f"./a.js?v={versions.fleet_hash}" in once and f"/static/a.js?v={versions.fleet_hash}" in once
    assert "'/static/missing.js'" in once
