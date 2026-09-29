"""Spotify: links, the token, the distinct states and the backend's calls — against a fake HTTP layer."""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

from src.env_file import read_env, set_value
from src.music.backend import MusicError, Track
from src.music.library import spotify_check
from src.music.spotify import (
    NO_DEVICE,
    NOT_CONFIGURED,
    NOT_PREMIUM,
    OK,
    TOKEN_EXPIRED,
    UNKNOWN,
    SpotifyBackend,
    SpotifyClient,
    SpotifyError,
    normalize,
    play_body,
)
from src.sessions.store import SESSION_FILE
from tests.fixtures.demo import build_demo_session

PC = {"id": "dev-pc", "name": "STUDIO-PC", "type": "Computer", "is_active": False, "is_restricted": False, "volume_percent": 65}
PHONE = {"id": "dev-phone", "name": "Phone", "type": "Smartphone", "is_active": True, "is_restricted": False}


class FakeSpotify:
    """Spotify's two hosts in memory: the token endpoint and the Web API."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Optional[dict[str, Any]]]] = []
        self.token_answer: tuple[int, dict[str, Any]] = (200, {"access_token": "tok-1", "expires_in": 3600})
        self.devices = [PHONE, PC]
        self.fail: dict[str, tuple[int, dict[str, Any]]] = {}  # "PUT /me/player/play" → answer
        self.unauthorized_once = False
        self.down = False

    def __call__(self, method: str, url: str, headers: dict[str, str], body: Optional[bytes]) -> tuple[int, dict[str, str], bytes]:
        if self.down:
            raise OSError("network down")
        u = urllib.parse.urlparse(url)
        if u.netloc == "accounts.spotify.com":
            self.calls.append(("TOKEN", "", dict(urllib.parse.parse_qsl(body.decode()))))
            status, data = self.token_answer
            return status, {}, json.dumps(data).encode()
        path = u.path.removeprefix("/v1")
        query = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, path, json.loads(body) if body else query or None))
        if self.unauthorized_once:
            self.unauthorized_once = False
            return 401, {}, b'{"error": {"status": 401, "message": "The access token expired"}}'
        key = f"{method} {path}"
        if key in self.fail:
            status, data = self.fail[key]
            return status, {"Retry-After": "7"}, json.dumps(data).encode()
        if key == "GET /me/player/devices":
            return 200, {}, json.dumps({"devices": self.devices}).encode()
        return 204, {}, b""

    def api(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "TOKEN"]


ENV = {"SPOTIFY_CLIENT_ID": "client-123", "SPOTIFY_REFRESH_TOKEN": "refresh-1", "SPOTIFY_DEVICE_NAME": "studio-pc"}


def client(fake: FakeSpotify, env: Optional[dict[str, str]] = None, saved: Optional[dict[str, str]] = None) -> SpotifyClient:
    env = dict(ENV if env is None else env)

    def save(key: str, value: str) -> None:
        env[key] = value
        if saved is not None:
            saved[key] = value
    return SpotifyClient(http=fake, env=lambda k: env.get(k, ""), save=save)


def state_of(fn: Any) -> str:
    with pytest.raises(SpotifyError) as err:
        fn()
    return err.value.state


def test_links_become_uris() -> None:
    assert normalize("spotify:playlist:37i9dQZF1DX4sWSpwq3LiO") == "spotify:playlist:37i9dQZF1DX4sWSpwq3LiO"
    assert normalize("https://open.spotify.com/playlist/37i9dQZF1DX4sWSpwq3LiO?si=abc123") == "spotify:playlist:37i9dQZF1DX4sWSpwq3LiO"
    assert normalize("https://open.spotify.com/intl-es/album/4aawyAB9vmqN3uQ7FjRGTy") == "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"
    assert play_body("spotify:track:abc") == {"uris": ["spotify:track:abc"]}
    assert play_body("spotify:playlist:abc") == {"context_uri": "spotify:playlist:abc"}
    with pytest.raises(MusicError) as err:
        normalize("https://example.com/playlist/abc")
    assert err.value.code == "bad_spotify_link"


def test_the_token_refreshes_rotates_and_says_why_it_cannot() -> None:
    fake, saved = FakeSpotify(), {}
    fake.token_answer = (200, {"access_token": "tok-1", "expires_in": 3600, "refresh_token": "refresh-2"})
    c = client(fake, saved=saved)
    assert c.token() == "tok-1" and saved == {"SPOTIFY_REFRESH_TOKEN": "refresh-2"}  # rotated: kept
    assert fake.calls[0][2] == {"grant_type": "refresh_token", "refresh_token": "refresh-1", "client_id": "client-123"}
    c.token()
    assert len(fake.calls) == 1  # cached until it nears expiry

    assert state_of(client(FakeSpotify(), env={}).token) == NOT_CONFIGURED  # no network call at all
    bad = FakeSpotify()
    bad.token_answer = (400, {"error": "invalid_grant", "error_description": "Invalid refresh token"})  # observed live
    assert state_of(client(bad).token) == TOKEN_EXPIRED
    bad.token_answer = (400, {"error": "invalid_client"})
    assert state_of(client(bad).token) == NOT_CONFIGURED
    bad.token_answer = (503, {})
    assert state_of(client(bad).token) == UNKNOWN
    down = FakeSpotify()
    down.down = True
    with pytest.raises(SpotifyError) as err:
        client(down).token()
    assert err.value.state == UNKNOWN and "not reachable" in err.value.detail


def test_api_errors_are_distinct_states() -> None:
    fake = FakeSpotify()
    c = client(fake)
    fake.unauthorized_once = True  # an access token that expired early: one refresh, one retry
    assert c.call("PUT", "/me/player/pause") == {}
    assert [x[0] for x in fake.calls].count("TOKEN") == 2
    fake.fail["PUT /me/player/play"] = (403, {"error": {"status": 403, "message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}})
    assert state_of(lambda: c.call("PUT", "/me/player/play")) == NOT_PREMIUM
    fake.fail["PUT /me/player/play"] = (404, {"error": {"status": 404, "message": "Player command failed: No active device found", "reason": "NO_ACTIVE_DEVICE"}})
    assert state_of(lambda: c.call("PUT", "/me/player/play")) == NO_DEVICE
    fake.fail["PUT /me/player/play"] = (429, {})
    with pytest.raises(SpotifyError) as err:
        c.call("PUT", "/me/player/play")
    assert err.value.state == UNKNOWN and "7 s" in err.value.detail
    fake.fail["GET /me"] = (401, {"error": {"status": 401, "message": "Missing/invalid/expired access token"}})
    assert state_of(lambda: c.call("GET", "/me")) == TOKEN_EXPIRED


def test_the_desktop_app_on_this_pc_is_the_device() -> None:
    fake = FakeSpotify()
    assert client(fake).device()["id"] == "dev-pc"  # by name (SPOTIFY_DEVICE_NAME), not the active phone
    fake.devices = [PHONE]
    with pytest.raises(SpotifyError) as err:
        client(fake).device()
    assert err.value.state == NO_DEVICE and "Phone" in err.value.detail


def wait_for(cond: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_backend_plays_fades_pauses_and_stops_by_volume_steps() -> None:
    fake, events = FakeSpotify(), []
    b = SpotifyBackend(lambda *e: events.append(e), client=client(fake), sleep=lambda s: None)
    b.play(3, Track("spotify", "spotify:playlist:abc", "Spotify playlist abc"), 60, 1.0)  # 2 steps of 0.5 s
    wait_for(lambda: len(fake.api()) == 5)
    b.pause(1.0)
    wait_for(lambda: len(fake.api()) == 8)
    b.resume(60, 0.5)
    wait_for(lambda: len(fake.api()) == 11)
    b.stop(0.5)
    wait_for(lambda: len(fake.api()) == 14)
    vol = lambda c: ("vol", int(c[2]["volume_percent"])) if c[1] == "/me/player/volume" else (c[0], c[1])  # noqa: E731
    assert [vol(c) for c in fake.api()] == [
        ("GET", "/me/player/devices"), ("vol", 0), ("PUT", "/me/player/play"), ("vol", 30), ("vol", 60),  # play, fade in
        ("vol", 30), ("vol", 0), ("PUT", "/me/player/pause"),  # pause: fade out, then pause
        ("vol", 0), ("PUT", "/me/player/play"), ("vol", 60),  # resume
        ("vol", 0), ("PUT", "/me/player/pause"), ("vol", 65),  # stop: fade, pause, the app's own volume back
    ]
    play = next(c for c in fake.api() if c[1] == "/me/player/play")
    assert play[2] == {"context_uri": "spotify:playlist:abc"}
    assert all(c[2].get("device_id") == "dev-pc" for c in fake.api() if c[1] == "/me/player/volume")
    assert events == [("playing", 3, "Spotify playlist abc on STUDIO-PC")]
    # A newer command cuts a fade short (the latest command wins): play, then stop at once.
    fake.calls.clear()
    b._transitions += 1  # as if a stop were already queued behind the play
    b._play(4, Track("spotify", "spotify:track:t", "t"), 60, 1.0)
    b._transitions -= 1
    assert [vol(c) for c in fake.api()] == [("GET", "/me/player/devices"), ("vol", 0), ("PUT", "/me/player/play")]
    # A resume after a server restart carries on from the desktop app's own position: no context sent.
    fake.calls.clear()
    b._play(5, Track("spotify", "spotify:playlist:abc", "Spotify playlist abc"), 60, 0, carry_on=True)
    play = next(c for c in fake.api() if c[1] == "/me/player/play")
    assert play[2] == {"device_id": "dev-pc"}
    b.close()


def test_backend_reports_each_failure_as_its_own_error() -> None:
    fake, events = FakeSpotify(), []
    b = SpotifyBackend(lambda *e: events.append(e), client=client(fake), sleep=lambda s: None)
    fake.devices = []
    b.play(1, Track("spotify", "spotify:track:x", "Spotify track x"), 50, 0)
    wait_for(lambda: events)
    assert events[-1][0] == "error" and "not open on this PC" in events[-1][2]
    assert b.status() == (NO_DEVICE, events[-1][2])  # the readiness check sees the same state
    fake.devices = [PC]
    fake.fail["PUT /me/player/play"] = (403, {"error": {"reason": "PREMIUM_REQUIRED", "message": "Premium required"}})
    b.play(2, Track("spotify", "spotify:track:x", "Spotify track x"), 50, 0)
    wait_for(lambda: len(events) == 2)
    assert events[-1] == ("error", 2, "Spotify Premium is needed to control playback from the app")
    b.close()


def test_status_is_cached_and_never_folds_unknown_into_ok() -> None:
    fake = FakeSpotify()
    b = SpotifyBackend(lambda *e: None, client=client(fake))
    assert b.status()[0] == OK
    n = len(fake.calls)
    assert b.status()[0] == OK and len(fake.calls) == n  # within 30 s: no new request
    fake.down = True
    assert b.status(fresh=True)[0] == UNKNOWN
    assert spotify_check(None)["state"] == "unknown"
    assert spotify_check((UNKNOWN, "x"))["state"] == "unknown"
    assert spotify_check((TOKEN_EXPIRED, "x"))["state"] == "warn"
    assert spotify_check((OK, "ready"))["state"] == "ok"


def test_env_file_keeps_other_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# secrets\nOTHER='kept'\nSPOTIFY_REFRESH_TOKEN=old\n", encoding="utf-8")
    set_value("SPOTIFY_REFRESH_TOKEN", "new", env)
    set_value("SPOTIFY_CLIENT_ID", "abc", env)
    assert env.read_text(encoding="utf-8") == "# secrets\nOTHER='kept'\nSPOTIFY_REFRESH_TOKEN=new\nSPOTIFY_CLIENT_ID=abc\n"
    assert read_env(env) == {"OTHER": "kept", "SPOTIFY_REFRESH_TOKEN": "new", "SPOTIFY_CLIENT_ID": "abc"}


def test_a_session_with_spotify_gets_the_spotify_check(client, isolated_env: Path) -> None:  # noqa: F811 — the app fixture
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "sp", isolated_env / "sessions.local.yaml")
    checks = {c["key"] for c in client.get(f"/api/sessions/{sid}").json()["readiness"]}
    assert "spotify" not in checks  # no Spotify in the plan: no check, no request
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    raw["sections"][0]["items"][0]["music"] = {"source": "spotify", "uri": "https://open.spotify.com/playlist/abc123"}
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    check = next(c for c in client.get(f"/api/sessions/{sid}").json()["readiness"] if c["key"] == "spotify")
    assert check["state"] == "warn" and "not set up" in check["detail"]  # empty .env: says so, no network


def test_a_pasted_spotify_link_plays_by_hand(isolated_env: Path) -> None:
    from src.config import load_config
    from src.live.actions import run_action
    from src.live.hub import LiveHub
    from src.music.service import MusicService
    from src.sessions.store import SessionStore
    from tests.test_music import FakeBackend

    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "paste", isolated_env / "sessions.local.yaml")
    hub = LiveHub(SessionStore(load_config()))
    made: dict[str, FakeBackend] = {}
    music = MusicService(hub, {k: (lambda sink, k=k: made.setdefault(k, FakeBackend(sink, k))) for k in ("file", "spotify")})
    hub.activate(sid)
    run_action(hub, "music_play", "https://open.spotify.com/playlist/37i9dQZF1DX4sWSpwq3LiO?si=x")
    assert made["spotify"].calls[-1] == ("play", "Spotify playlist 37i9dQ", 80, 2.0) and music.owner is None
    with pytest.raises(MusicError):
        run_action(hub, "music_play", "https://example.com/nope")


def test_the_login_asks_for_the_account_type_and_never_guesses_it() -> None:
    import importlib

    from src.music.spotify import SCOPES

    login = importlib.import_module("scripts.spotify_login")
    assert "user-read-private" in SCOPES.split()  # GET /me only returns ``product`` with this scope (#109)
    assert login.account_line("premium") == "✅ Account: premium"
    free = login.account_line("free")
    assert free.startswith("⚠️ Account: free") and "needs Premium" in free
    for missing in (None, ""):  # an old token without the scope: say so, claim nothing about Premium
        line = login.account_line(missing)
        assert "not checked" in line and "Premium" not in line and "premium" not in line
