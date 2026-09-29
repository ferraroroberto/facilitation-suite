"""One-time Spotify login for the app's music (PKCE — no client secret anywhere).

Before: create a Spotify developer app (README → Music → Spotify setup), add
the redirect URI ``http://127.0.0.1:8765/callback`` to it, and put its client
id in ``.env`` as ``SPOTIFY_CLIENT_ID=...``. Then, on this PC:

    .venv/Scripts/python.exe scripts/spotify_login.py

It opens the browser on Spotify's consent page, catches the redirect on
127.0.0.1, trades the code for tokens and writes ``SPOTIFY_REFRESH_TOKEN`` to
``.env`` (gitignored; the token is never printed or logged) — the same login
as Settings → Music → Connect Spotify (``src/music/spotify_login.py``). Then it checks
the account is Premium and lists the devices Spotify sees — the desktop app
on this PC must be among them. ``tray.bat --restart`` is not needed: the app
reads ``.env`` again on its next Spotify call.
"""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
import webbrowser
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import env_path  # noqa: E402
from src.env_file import get_value, set_value  # noqa: E402
from src.logger import configure_logging  # noqa: E402
from src.music.spotify import CLIENT_ID_KEY, REFRESH_KEY, SpotifyClient, SpotifyError  # noqa: E402
from src.music.spotify_login import (  # noqa: E402
    DEFAULT_PORT,
    Callback,
    LoginError,
    authorize_url,
    exchange,
    pkce_pair,
    redirect_uri,
)

logger = logging.getLogger("spotify_login")


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
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the redirect URI's port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="print the URL instead of opening the browser")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # the ✅/⚠️ lines on a Windows console
    configure_logging(to_file=False)
    client_id = get_value(CLIENT_ID_KEY)
    if not client_id:
        logger.error(f"❌ No {CLIENT_ID_KEY} in {env_path()} — create the Spotify developer app first (README → Music → Spotify setup).")
        return 1
    redirect = redirect_uri(args.port)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, redirect, challenge, state)
    logger.info(f"ℹ️ Log in to Spotify and allow the app (redirect URI must be {redirect}).")
    try:
        callback = Callback(args.port, state)
        if args.no_browser:
            logger.info("ℹ️ Open this address: %s", url)
        else:
            webbrowser.open(url)
        set_value(REFRESH_KEY, exchange(client_id, callback.wait(), redirect, verifier))
    except LoginError as exc:
        raise SystemExit(f"❌ {exc}") from None
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
