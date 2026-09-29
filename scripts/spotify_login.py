"""One-time Spotify login for the app's music (PKCE — no client secret anywhere).

Before: create a Spotify developer app (README → Music → Spotify setup), add
the redirect URI ``http://127.0.0.1:8765/callback`` to it, and put its client
id in ``.env`` as ``SPOTIFY_CLIENT_ID=...``. Then, on this PC:

    .venv/Scripts/python.exe scripts/spotify_login.py

It opens the browser on Spotify's consent page, catches the redirect on
127.0.0.1, trades the code for tokens and writes ``SPOTIFY_REFRESH_TOKEN`` to
``.env`` (gitignored; the token is never printed or logged). Then it checks
the account is Premium and lists the devices Spotify sees — the desktop app
on this PC must be among them. ``tray.bat --restart`` is not needed: the app
reads ``.env`` again on its next Spotify call.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import logging
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import env_path  # noqa: E402
from src.env_file import get_value, set_value  # noqa: E402
from src.logger import configure_logging  # noqa: E402
from src.music.spotify import (  # noqa: E402
    CLIENT_ID_KEY,
    REFRESH_KEY,
    SCOPES,
    TOKEN_URL,
    SpotifyClient,
    SpotifyError,
    parse_json,
    urllib_http,
)

logger = logging.getLogger("spotify_login")
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
WAIT_S = 300


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "scope": SCOPES, "redirect_uri": redirect_uri,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
    })


def wait_for_code(port: int, state: str) -> str:
    """Serve one redirect on 127.0.0.1:<port> and return its ``code``."""
    got: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server's name
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_error(404)
                return
            ok = q.get("state") == state and "code" in q
            got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            text = "Spotify is connected — you can close this tab." if ok else f"Spotify login failed: {q.get('error', 'unexpected answer')}"
            self.wfile.write(f"<!doctype html><meta charset='utf-8'><p style='font:18px sans-serif'>{text}</p>".encode())
            done.set()

        def log_message(self, fmt: str, *args: object) -> None:  # the URL carries the code: never log it
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if not done.wait(WAIT_S):
            raise SystemExit(f"❌ No answer from Spotify within {WAIT_S // 60} minutes — run the login again")
    finally:
        server.shutdown()
    if got.get("state") != state:
        raise SystemExit("❌ The answer did not match this login (state mismatch) — run the login again")
    if "code" not in got:
        raise SystemExit(f"❌ Spotify said: {got.get('error', 'no code')}")
    return got["code"]


def exchange(client_id: str, code: str, redirect_uri: str, verifier: str) -> str:
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                   "client_id": client_id, "code_verifier": verifier}).encode()
    status, _, raw = urllib_http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    data = parse_json(raw)
    if status != 200 or not data.get("refresh_token"):
        raise SystemExit(f"❌ Spotify refused the login ({status} {data.get('error', '')} {data.get('error_description', '')})".strip())
    return str(data["refresh_token"])


def account_line(product: Optional[str]) -> str:
    """The login's account line. No ``product`` (a token without ``user-read-private``)
    is "not checked" — never read as "not Premium"."""
    if not product:
        return "ℹ️ Account: not checked (Spotify did not say the account type)"
    if product == "premium":
        return "✅ Account: premium"
    return f"⚠️ Account: {product} — playback control needs Premium"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765, help="the redirect URI's port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL instead of opening the browser")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # the ✅/⚠️ lines on a Windows console
    configure_logging(to_file=False)
    client_id = get_value(CLIENT_ID_KEY)
    if not client_id:
        logger.error(f"❌ No {CLIENT_ID_KEY} in {env_path()} — create the Spotify developer app first (README → Music → Spotify setup).")
        return 1
    redirect_uri = f"http://127.0.0.1:{args.port}/callback"
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, redirect_uri, challenge, state)
    logger.info(f"ℹ️ Log in to Spotify and allow the app (redirect URI must be {redirect_uri}).")
    if args.no_browser:
        logger.info("ℹ️ Open this address: %s", url)
    else:
        webbrowser.open(url)
    code = wait_for_code(args.port, state)
    set_value(REFRESH_KEY, exchange(client_id, code, redirect_uri, verifier))
    logger.info(f"✅ Refresh token saved to {env_path()}")

    client = SpotifyClient()
    try:
        me = client.call("GET", "/me")
        logger.info(account_line(me.get("product")))
        devices = client.call("GET", "/me/player/devices").get("devices") or []
        logger.info("ℹ️ Devices Spotify sees: " + (", ".join(f"{d.get('name')} ({d.get('type')})" for d in devices) or "none — open the Spotify desktop app"))
        client.device()
        logger.info("✅ The desktop app on this PC is ready")
    except SpotifyError as exc:
        logger.warning(f"⚠️ {exc.state}: {exc.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
