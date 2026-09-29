"""The Spotify login (PKCE — no client secret anywhere), shared by the terminal
script (``scripts/spotify_login.py``) and the app's **Connect Spotify** button
(Settings → Music, this PC only).

One login: open Spotify's consent page in the browser, catch the redirect on
``http://127.0.0.1:<port>/callback`` (the redirect URI registered in the
Spotify developer app, default port ``8765``), trade the code for tokens and
write ``SPOTIFY_REFRESH_TOKEN`` to ``.env``. The token, the code and the
client id are never logged, printed or sent to a page.

``Login`` runs it in a background thread for the app: ``start()`` returns at
once, ``snapshot()`` says ``idle`` / ``waiting`` / ``done`` / ``failed`` with
a line for the page.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from typing import Any, Optional

from src.env_file import get_value, set_value
from src.music.spotify import CLIENT_ID_KEY, REFRESH_KEY, SCOPES, TOKEN_URL, Http, parse_json, urllib_http

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
DEFAULT_PORT = 8765  # the redirect URI registered in the Spotify developer app
WAIT_S = 300

IDLE, WAITING, DONE, FAILED = "idle", "waiting", "done", "failed"


class LoginError(Exception):
    """A login that did not end with a saved token; the message is safe to show."""


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect: str, challenge: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "scope": SCOPES, "redirect_uri": redirect,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
    })


class Callback:
    """Serve one redirect on ``127.0.0.1:<port>`` and hand back its ``code``.
    Binding happens in the constructor, so a busy port fails before the browser opens."""

    def __init__(self, port: int, state: str) -> None:
        self.state = state
        self.got: dict[str, str] = {}
        self.done = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — http.server's name
                url = urllib.parse.urlparse(self.path)
                if url.path != "/callback":
                    self.send_error(404)
                    return
                q = dict(urllib.parse.parse_qsl(url.query))
                ok = q.get("state") == owner.state and "code" in q
                owner.got.update(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                text = ("Spotify is connected — you can close this tab." if ok
                        else f"Spotify login failed: {q.get('error', 'unexpected answer')}")
                self.wfile.write(f"<!doctype html><meta charset='utf-8'><p style='font:18px sans-serif'>{text}</p>".encode())
                owner.done.set()

            def log_message(self, fmt: str, *args: object) -> None:  # the URL carries the code: never log it
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, name="spotify-callback", daemon=True).start()

    def wait(self, timeout_s: float = WAIT_S) -> str:
        try:
            if not self.done.wait(timeout_s):
                raise LoginError(f"No answer from Spotify within {round(timeout_s / 60)} minutes — run the login again")
        finally:
            self.close()
        if self.got.get("state") != self.state:
            raise LoginError("The answer did not match this login (state mismatch) — run the login again")
        if "code" not in self.got:
            raise LoginError(f"Spotify said: {self.got.get('error', 'no code')}")
        return self.got["code"]

    def close(self) -> None:
        self.done.set()
        self.server.shutdown()
        self.server.server_close()


def exchange(client_id: str, code: str, redirect: str, verifier: str, http: Http = urllib_http) -> str:
    """The authorization code → the refresh token."""
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                   "client_id": client_id, "code_verifier": verifier}).encode()
    try:
        status, _, raw = http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    except OSError as exc:
        raise LoginError(f"Spotify is not reachable ({type(exc).__name__}) — try again") from exc
    data = parse_json(raw)
    if status != 200 or not data.get("refresh_token"):
        raise LoginError(f"Spotify refused the login ({status} {data.get('error', '')} {data.get('error_description', '')})".strip())
    return str(data["refresh_token"])


class Login:
    """The app's login: one at a time, in a background thread; a new start replaces a waiting one."""

    def __init__(self, *, port: int = DEFAULT_PORT, http: Http = urllib_http,
                 open_browser: Callable[[str], Any] = webbrowser.open,
                 env: Callable[[str], str] = get_value, save: Callable[[str, str], Any] = set_value,
                 on_saved: Optional[Callable[[], Any]] = None) -> None:
        self.port, self.http, self.open_browser, self.env, self.save = port, http, open_browser, env, save
        self.on_saved = on_saved
        self.wait_s: float = WAIT_S
        self._lock = threading.Lock()
        self._callback: Optional[Callback] = None
        self._run = 0
        self.state, self.detail, self.at = IDLE, "", 0.0

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "detail": self.detail, "at": self.at or None}

    def _set(self, run: int, state: str, detail: str) -> None:
        with self._lock:
            if run == self._run:
                self.state, self.detail, self.at = state, detail, time.time()

    def start(self) -> dict[str, Any]:
        """Open the consent page and wait for the redirect in the background.
        Raises ``LoginError`` at once when there is no client id or the port is busy."""
        client_id = self.env(CLIENT_ID_KEY)
        if not client_id:
            raise LoginError(f"No {CLIENT_ID_KEY} in .env — create the Spotify developer app first (README → Spotify setup)")
        with self._lock:
            if self._callback is not None:
                self._callback.close()  # a login still waiting: this one replaces it
                self._callback = None
            self._run += 1
            run = self._run
            verifier, challenge = pkce_pair()
            state = secrets.token_urlsafe(16)
            try:
                callback = Callback(self.port, state)
            except OSError as exc:
                raise LoginError(f"Port {self.port} is busy (is scripts/spotify_login.py running?) — close it and try again") from exc
            self._callback = callback
            self.state, self.detail, self.at = WAITING, "Log in to Spotify in the browser window that opened", time.time()
        redirect = redirect_uri(self.port)
        logger.info("ℹ️ spotify: login started from the app (redirect %s)", redirect)
        threading.Thread(target=self._finish, args=(run, callback, client_id, redirect, verifier),
                         name="spotify-login", daemon=True).start()
        self.open_browser(authorize_url(client_id, redirect, challenge, state))
        return self.snapshot()

    def _finish(self, run: int, callback: Callback, client_id: str, redirect: str, verifier: str) -> None:
        try:
            code = callback.wait(self.wait_s)
            with self._lock:
                if run != self._run:
                    return  # replaced by a newer login
            self.save(REFRESH_KEY, exchange(client_id, code, redirect, verifier, self.http))
        except LoginError as exc:
            if run == self._run:
                logger.warning("⚠️ spotify: login from the app failed: %s", exc)
            self._set(run, FAILED, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — the page must learn it ended
            logger.exception("❌ spotify: login from the app failed")
            self._set(run, FAILED, f"The login failed ({type(exc).__name__}) — see the log")
            return
        finally:
            with self._lock:
                if self._callback is callback:
                    self._callback = None
        logger.info("✅ spotify: login from the app saved the refresh token to .env")
        if self.on_saved is not None:
            try:
                self.on_saved()
            except Exception:  # noqa: BLE001 — the token is saved either way
                logger.exception("❌ spotify: after-login refresh failed")
        self._set(run, DONE, "Connected — the login is saved in .env")
