"""Spotify through the official Web API, playing on the Spotify desktop app of this PC.

Setup is one login (Settings → Music → Connect Spotify, or
``scripts/spotify_login.py``; PKCE — no client secret):
``.env`` then holds ``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_REFRESH_TOKEN``
(never the repo, never ``config.json``). A refresh token Spotify rotates is
written back to ``.env``. Controlling playback needs a **Premium** account and
the desktop app open on this PC: the backend targets the ``Computer`` device
named like this PC (``SPOTIFY_DEVICE_NAME`` in ``.env`` overrides the name),
else the active computer, and starts playback there by its device id — an idle
app is woken without a separate transfer.

Fades step the volume (``PUT /me/player/volume``) every ``STEP_S`` — at most
two requests a second, far under Spotify's rolling rate limit; a 429 says so
instead of retrying. Spotify has no "stop": a stop fades out, pauses, and puts
the app's volume back where it was before the music started.

Every failure is one of distinct, visible states (``status()`` and the error
the presenter shows): ``not_configured`` (no client id / refresh token, or a
wrong client id), ``token_expired`` (the refresh token was revoked or
expired — log in again), ``no_device`` (the desktop app is not open on this
PC), ``not_premium``, ``ok`` — and ``unknown`` when it could not be
established (Spotify unreachable, rate-limited, a 5xx), never folded into ok.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Optional

from src.env_file import get_value, set_value
from src.music.backend import EventSink, MusicError, Track

logger = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CLIENT_ID_KEY = "SPOTIFY_CLIENT_ID"
REFRESH_KEY = "SPOTIFY_REFRESH_TOKEN"
DEVICE_KEY = "SPOTIFY_DEVICE_NAME"
# user-read-private: GET /me returns the account type (``product``) only with it (#109)
SCOPES = "user-read-playback-state user-modify-playback-state user-read-private"
STEP_S = 0.5  # one volume request per half second while fading
TIMEOUT_S = 5.0
STATUS_TTL_S = 30.0  # the readiness check reuses a recent answer
CONFIGURED_TTL_S = 10.0

OK, NOT_CONFIGURED, TOKEN_EXPIRED, NO_DEVICE, NOT_PREMIUM, UNKNOWN = (
    "ok", "not_configured", "token_expired", "no_device", "not_premium", "unknown")
MESSAGES = {
    NOT_CONFIGURED: "Spotify is not set up — put SPOTIFY_CLIENT_ID in .env, then Settings → Music → Connect Spotify",
    TOKEN_EXPIRED: "The Spotify login expired or was revoked — Settings → Music → Reconnect Spotify",
    NO_DEVICE: "Spotify is not open on this PC — open the Spotify desktop app (same account) and try again",
    NOT_PREMIUM: "Spotify Premium is needed to control playback from the app",
}

# (method, url, headers, body) → (status, headers, body); raises OSError when unreachable.
Http = Callable[[str, str, dict[str, str], Optional[bytes]], tuple[int, dict[str, str], bytes]]


class SpotifyError(Exception):
    def __init__(self, state: str, detail: str = "") -> None:
        super().__init__(detail or MESSAGES.get(state, state))
        self.state = state
        self.detail = str(self)


def urllib_http(method: str, url: str, headers: dict[str, str], body: Optional[bytes]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 — fixed https hosts
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


# ------------------------------------------------------------------ links

_LINK = re.compile(r"^https?://open\.spotify\.com/(?:intl-[A-Za-z-]+/)?(playlist|album|artist|track|show|episode)/([A-Za-z0-9]+)")
_URI = re.compile(r"^spotify:(playlist|album|artist|track|show|episode):([A-Za-z0-9]+)$")


def normalize(ref: str) -> str:
    """A ``spotify:<kind>:<id>`` URI from a URI or an ``open.spotify.com`` link."""
    ref = (ref or "").strip()
    m = _URI.match(ref) or _LINK.match(ref)
    if not m:
        raise MusicError(422, "bad_spotify_link", "Not a Spotify playlist, album, artist or track link")
    return f"spotify:{m.group(1)}:{m.group(2)}"


def is_link(ref: str) -> bool:
    return bool(_URI.match((ref or "").strip()) or _LINK.match((ref or "").strip()))


def label(uri: str) -> str:
    _, kind, ident = uri.split(":", 2)
    return f"Spotify {kind} {ident[:6]}"


def play_body(uri: str) -> dict[str, Any]:
    kind = uri.split(":")[1]
    return {"uris": [uri]} if kind in ("track", "episode") else {"context_uri": uri}


# ------------------------------------------------------------------ client


def parse_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class SpotifyClient:
    """The access token (refreshed from ``.env``) and the Web API calls. Thread-safe."""

    def __init__(self, http: Http = urllib_http, env: Callable[[str], str] = get_value,
                 save: Callable[[str, str], Any] = set_value) -> None:
        self.http, self.env, self.save = http, env, save
        self._lock = threading.Lock()
        self._token = ""
        self._expires = 0.0

    def configured(self) -> bool:
        return bool(self.env(CLIENT_ID_KEY) and self.env(REFRESH_KEY))

    def forget(self) -> None:
        """Drop the access token: the next call refreshes from ``.env`` (a new login)."""
        with self._lock:
            self._token, self._expires = "", 0.0

    def _send(self, method: str, url: str, headers: dict[str, str], body: Optional[bytes]) -> tuple[int, dict[str, str], dict[str, Any]]:
        try:
            status, hdrs, raw = self.http(method, url, headers, body)
        except OSError as exc:
            raise SpotifyError(UNKNOWN, f"Spotify is not reachable ({type(exc).__name__})") from exc
        return status, hdrs, parse_json(raw)

    def _refresh(self) -> None:
        cid, refresh = self.env(CLIENT_ID_KEY), self.env(REFRESH_KEY)
        if not cid or not refresh:
            raise SpotifyError(NOT_CONFIGURED)
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid}).encode()
        status, _, data = self._send("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status == 200 and data.get("access_token"):
            self._token = str(data["access_token"])
            self._expires = time.monotonic() + float(data.get("expires_in") or 3600)
            new = str(data.get("refresh_token") or "")
            if new and new != refresh:
                self.save(REFRESH_KEY, new)  # PKCE refresh tokens rotate: keep the new one
                logger.info("ℹ️ spotify: refresh token rotated and saved to .env")
            return
        error = str(data.get("error") or "")
        if error == "invalid_grant":
            raise SpotifyError(TOKEN_EXPIRED)
        if error == "invalid_client":
            raise SpotifyError(NOT_CONFIGURED, "SPOTIFY_CLIENT_ID in .env is not a valid Spotify app — check it, then Settings → Music → Connect Spotify")
        raise SpotifyError(UNKNOWN, f"Spotify's login service answered {status} {error}".strip())

    def token(self) -> str:
        with self._lock:
            if not self._token or time.monotonic() > self._expires - 60:
                self._refresh()
            return self._token

    def call(self, method: str, path: str, body: Optional[dict[str, Any]] = None,
             query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = API + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
        payload = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}
            status, hdrs, data = self._send(method, url, headers, payload)
            if status == 401 and attempt == 0:
                with self._lock:
                    self._token = ""  # expired early: one fresh token, one retry
                continue
            break
        if status < 300:
            return data
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        reason, message = str(err.get("reason") or ""), str(err.get("message") or "")
        if status == 403 and (reason == "PREMIUM_REQUIRED" or "premium" in message.lower()):
            raise SpotifyError(NOT_PREMIUM)
        if status == 404 and (reason == "NO_ACTIVE_DEVICE" or "device" in message.lower()):
            raise SpotifyError(NO_DEVICE)
        if status == 401:
            raise SpotifyError(TOKEN_EXPIRED)
        if status == 429:
            raise SpotifyError(UNKNOWN, f"Spotify is rate-limiting the app — try again in {hdrs.get('Retry-After', 'a few')} s")
        raise SpotifyError(UNKNOWN, f"Spotify answered {status}{': ' + message if message else ''}")

    def device(self) -> dict[str, Any]:
        """The desktop app on this PC (by name), else the active computer, else any computer."""
        devices = self.call("GET", "/me/player/devices").get("devices") or []
        computers = [d for d in devices if str(d.get("type", "")).lower() == "computer" and not d.get("is_restricted")]
        name = (self.env(DEVICE_KEY) or socket.gethostname()).lower()
        pick = (next((d for d in computers if str(d.get("name", "")).lower() == name), None)
                or next((d for d in computers if d.get("is_active")), None)
                or (computers[0] if computers else None))
        if pick is None:
            others = ", ".join(str(d.get("name")) for d in devices)
            raise SpotifyError(NO_DEVICE, MESSAGES[NO_DEVICE] + (f" (Spotify sees only: {others})" if others else ""))
        return pick


# ------------------------------------------------------------------ backend


class SpotifyBackend:
    kind = "spotify"

    def __init__(self, on_event: EventSink, *, client: Optional[SpotifyClient] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.on_event = on_event
        self.client = client or SpotifyClient()
        self._sleep = sleep
        self._queue: queue.Queue = queue.Queue()
        self._transitions = 0  # queued play/pause/resume/stop commands: a fade in progress gives way
        self._count_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.device: Optional[dict[str, Any]] = None
        self.volume_now: Optional[int] = None  # what Spotify's volume was last set to
        self.restore_volume: Optional[int] = None  # the app's own volume before the music started
        self.play_id = 0
        self._status: tuple[str, str, float] = ("", "", 0.0)
        self.checked_at: Optional[float] = None  # wall clock of the last status check (Settings → Music)
        self.status_device = ""  # the device the last status check found
        self._configured: tuple[bool, float] = (False, -CONFIGURED_TTL_S)

    # ---------------------------------------------------------------- interface

    def play(self, play_id: int, track: Track, volume: int, fade_in_s: float, start_s: float = 0.0) -> None:
        self._submit(lambda: self._play(play_id, track, volume, fade_in_s, start_s > 0), transition=True)

    def pause(self, fade_s: float) -> None:
        self._submit(lambda: self._pause(fade_s), transition=True)

    def resume(self, volume: int, fade_s: float) -> None:
        self._submit(lambda: self._resume(volume, fade_s), transition=True)

    def stop(self, fade_s: float) -> None:
        self._submit(lambda: self._stop(fade_s), transition=True)

    def set_volume(self, volume: int) -> None:
        self._submit(lambda: self._volume(volume) if self.volume_now else None)

    def fade(self, to: int, seconds: float) -> None:
        self._submit(lambda: self._fade(self.volume_now or 0, to, seconds))

    def snapshot(self) -> dict[str, Any]:
        return {"device": (self.device or {}).get("name", ""), "status": self._status[0] or None}

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(("quit", None, False))
            self._thread.join(timeout=3)

    def configured(self) -> bool:
        """``.env`` holds a client id and a refresh token (re-read every few seconds)."""
        ok, at = self._configured
        if time.monotonic() - at >= CONFIGURED_TTL_S:
            ok = self.client.configured()
            self._configured = (ok, time.monotonic())
        return ok

    def status(self, *, fresh: bool = False) -> tuple[str, str]:
        """(state, detail) for the readiness list: token valid and the desktop app visible."""
        state, detail, at = self._status
        if not fresh and state and time.monotonic() - at < STATUS_TTL_S:
            return state, detail
        device = ""
        try:
            dev = self.client.device()
            device = str(dev.get("name") or "")
            state, detail = OK, f"Logged in · the desktop app on {device or 'this PC'} is ready"
        except SpotifyError as exc:
            state, detail = exc.state, exc.detail
        self._status = (state, detail, time.monotonic())
        self.checked_at, self.status_device = time.time(), device
        return state, detail

    def forget_login(self) -> None:
        """A new login was saved to ``.env``: drop the cached token and answers."""
        self.client.forget()
        self._status = ("", "", 0.0)
        self._configured = (False, -CONFIGURED_TTL_S)
        self.device = None

    # ------------------------------------------------------------------ worker

    def _submit(self, fn: Callable[[], Any], transition: bool = False) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="music-spotify", daemon=True)
            self._thread.start()
        if transition:
            with self._count_lock:
                self._transitions += 1
        self._queue.put(("do", fn, transition))

    def _run(self) -> None:
        while True:
            kind, fn, transition = self._queue.get()
            if kind == "quit":
                return
            if transition:
                with self._count_lock:
                    self._transitions -= 1
            try:
                fn()
            except SpotifyError as exc:
                self._status = (exc.state, exc.detail, time.monotonic())
                logger.warning("⚠️ spotify: %s (%s)", exc.state, exc.detail)
                self._emit("error", self.play_id, exc.detail)
            except Exception as exc:  # noqa: BLE001 — never a dead worker
                logger.exception("❌ spotify: command failed")
                self._emit("error", self.play_id, f"Spotify playback failed ({exc})")

    def _emit(self, event: str, play_id: int, detail: str = "") -> None:
        try:
            self.on_event(event, play_id, detail)
        except RuntimeError:  # the server's event loop is already closed
            pass

    def _interrupted(self) -> bool:
        with self._count_lock:
            return self._transitions > 0

    def _device_id(self) -> str:
        if self.device is None:
            self.device = self.client.device()
        return str(self.device["id"])

    def _volume(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        self.client.call("PUT", "/me/player/volume", query={"volume_percent": volume, "device_id": self._device_id()})
        self.volume_now = volume

    def _fade(self, start: int, to: int, seconds: float) -> bool:
        """Step the volume from ``start`` to ``to``; False when a newer command cut it short."""
        steps = max(1, round(seconds / STEP_S)) if seconds > 0 else 1
        for i in range(1, steps + 1):
            if self._interrupted():
                return False
            self._volume(round(start + (to - start) * i / steps))
            if i < steps:
                self._sleep(STEP_S)
        return True

    def _play(self, play_id: int, track: Track, volume: int, fade_in_s: float, carry_on: bool = False) -> None:
        """``carry_on`` (a resume after a restart): the desktop app keeps its own position — play on
        from there instead of starting the track or playlist over."""
        self.play_id = play_id
        self.device = self.client.device()  # the app may have been reopened since
        if self.restore_volume is None:
            self.restore_volume = self.device.get("volume_percent")
        self._volume(0 if fade_in_s > 0 else volume)
        self.client.call("PUT", "/me/player/play", body=None if carry_on else play_body(track.ref),
                         query={"device_id": self.device["id"]})
        self._status = (OK, "Playing", time.monotonic())
        logger.info("ℹ️ spotify: %s %s on %s (volume %d, fade in %.1f s)", "carry on with" if carry_on else "play",
                    track.ref, self.device.get("name"), volume, fade_in_s)
        self._emit("playing", play_id, f"{track.label} on {self.device.get('name', 'Spotify')}")
        if fade_in_s > 0:
            self._fade(0, volume, fade_in_s)

    def _pause(self, fade_s: float) -> None:
        if not self._fade(self.volume_now or 0, 0, fade_s):
            return
        self.client.call("PUT", "/me/player/pause", query={"device_id": self._device_id()})
        logger.info("ℹ️ spotify: paused")

    def _resume(self, volume: int, fade_s: float) -> None:
        self._volume(0)
        self.client.call("PUT", "/me/player/play", query={"device_id": self._device_id()})
        self._fade(0, volume, fade_s)

    def _stop(self, fade_s: float) -> None:
        if not self._fade(self.volume_now or 0, 0, fade_s):
            return
        self.client.call("PUT", "/me/player/pause", query={"device_id": self._device_id()})
        if self.restore_volume is not None:
            self._volume(int(self.restore_volume))  # the app keeps the volume it had before the music
        self.restore_volume = None
        self.volume_now = None
        logger.info("ℹ️ spotify: stopped")
