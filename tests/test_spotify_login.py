"""Settings → Music → Connect Spotify (#110): the same PKCE login as scripts/spotify_login.py,
from the app — against a fake token endpoint and a stand-in browser, never Spotify or the real .env."""

from __future__ import annotations

import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from src.env_file import read_env, set_value
from src.music import spotify_login
from src.music.spotify import REFRESH_KEY, TOKEN_URL

CLIENT_ID = "test-client-id-0123456789"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class FakeToken:
    """Spotify's token endpoint: trades a code for a refresh token."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.n = 0

    def __call__(self, method: str, url: str, headers: dict[str, str], body: Optional[bytes]) -> tuple[int, dict[str, str], bytes]:
        assert method == "POST" and url == TOKEN_URL
        form = dict(urllib.parse.parse_qsl((body or b"").decode()))
        if form.get("grant_type") == "refresh_token":  # the app's status check: never the real Spotify
            return 400, {}, b'{"error": "invalid_grant"}'
        self.calls.append(form)
        if form.get("code") == "bad":
            return 400, {}, b'{"error": "invalid_grant", "error_description": "Invalid authorization code"}'
        self.n += 1
        return 200, {}, f'{{"access_token": "a", "refresh_token": "refresh-secret-{self.n}", "expires_in": 3600}}'.encode()


class Browser:
    """The browser: remembers the consent URL; ``answer`` plays Spotify's redirect back to the app."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        return True

    def answer(self, code: str = "the-code", state: Optional[str] = None) -> str:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.urls[-1]).query))
        back = f"{q['redirect_uri']}?{urllib.parse.urlencode({'code': code, 'state': state or q['state']})}"
        with urllib.request.urlopen(back, timeout=5) as r:  # noqa: S310 — the loopback callback
            return r.read().decode()


@pytest.fixture
def app_login(isolated_env: Path) -> Any:
    from app.webapp.server import create_app

    set_value("SPOTIFY_CLIENT_ID", CLIENT_ID)
    app = create_app()
    login = app.state.spotify_login
    login.port, login.http, login.open_browser = _free_port(), FakeToken(), Browser()
    app.state.music.backends["spotify"].client.http = login.http
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c, login


def _until_login(c: TestClient, state: str) -> dict:
    end = time.time() + 5
    while True:
        body = c.get("/api/settings/spotify").json()
        if body["login"]["state"] == state or time.time() > end:
            return body
        time.sleep(0.05)


def _no_secrets(body: Any) -> None:
    text = str(body)
    assert CLIENT_ID not in text and "refresh-secret" not in text and "the-code" not in text


def test_connect_saves_the_token_and_reconnect_replaces_it(app_login: Any, isolated_env: Path) -> None:
    c, login = app_login
    before = c.get("/api/settings/spotify").json()
    assert before["state"] == "not_configured" and before["client_id_set"] and not before["logged_in"]
    _no_secrets(before)

    r = c.post("/api/settings/spotify/connect")
    assert r.status_code == 200 and r.json()["login"]["state"] == "waiting"
    _no_secrets(r.json())
    url = urllib.parse.urlparse(login.open_browser.urls[-1])
    q = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "accounts.spotify.com" and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == f"http://127.0.0.1:{login.port}/callback"
    assert "user-read-private" in q["scope"].split()
    assert "connected" in login.open_browser.answer()

    done = _until_login(c, "done")
    assert done["login"]["state"] == "done" and done["logged_in"]
    assert done["state"] == "token_expired"  # the fake's refresh answer: the status check really ran
    _no_secrets(done)
    assert read_env()[REFRESH_KEY] == "refresh-secret-1"
    sent = login.http.calls[-1]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "the-code" and sent["code_verifier"]

    # Reconnect: a second login replaces the token
    c.post("/api/settings/spotify/connect")
    login.open_browser.answer()
    assert _until_login(c, "done")["login"]["state"] == "done"
    assert read_env()[REFRESH_KEY] == "refresh-secret-2"
    assert list(read_env()).count(REFRESH_KEY) == 1


def test_a_refused_or_foreign_answer_fails_and_keeps_the_old_token(app_login: Any) -> None:
    c, login = app_login
    set_value(REFRESH_KEY, "old-token")
    c.post("/api/settings/spotify/connect")
    login.open_browser.answer(code="bad")
    failed = _until_login(c, "failed")
    assert failed["login"]["state"] == "failed" and "refused" in failed["login"]["detail"]
    c.post("/api/settings/spotify/connect")
    assert "failed" in login.open_browser.answer(state="someone-else")
    failed = _until_login(c, "failed")
    assert "state mismatch" in failed["login"]["detail"]
    assert read_env()[REFRESH_KEY] == "old-token"


def test_connect_needs_a_client_id_a_free_port_and_this_pc(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    app = create_app()
    login = app.state.spotify_login
    login.port, login.http, login.open_browser = _free_port(), FakeToken(), Browser()
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        r = c.post("/api/settings/spotify/connect")
        assert r.status_code == 409 and "SPOTIFY_CLIENT_ID" in r.json()["error"]["message"]
        set_value("SPOTIFY_CLIENT_ID", CLIENT_ID)
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", login.port))
            busy.listen()
            r = c.post("/api/settings/spotify/connect")
            assert r.status_code == 409 and "busy" in r.json()["error"]["message"]
        assert login.open_browser.urls == []  # nothing opened for a login that could not start
    with TestClient(app, client=("100.64.0.9", 5000), headers={"Authorization": "Bearer x"}) as phone:
        assert phone.post("/api/settings/spotify/connect").status_code in (401, 403)


def test_the_script_uses_the_shared_login() -> None:
    import importlib

    script = importlib.import_module("scripts.spotify_login")
    assert script.Callback is spotify_login.Callback and script.exchange is spotify_login.exchange
    assert spotify_login.DEFAULT_PORT == 8765  # the redirect URI registered in the Spotify app
