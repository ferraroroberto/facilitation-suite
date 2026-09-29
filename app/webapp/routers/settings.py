"""Settings shared by every session: OBS connection and profiles, the chat reader, the phone remote,
the appearance (light/dark) of the facilitator's screens, the defaults new sessions start from with
the stage library (#110), and the Spotify account (status and the Connect button, this PC only)."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError

from app.webapp.errors import AppError, is_local, require_local
from src import defaults, library
from src import settings as settings_file
from src.certs import cert_hostname
from src.config import profiles
from src.env_file import get_value
from src.music.spotify import CLIENT_ID_KEY, REFRESH_KEY
from src.music.spotify_login import LoginError
from src.sessions.model import StageFont
from src.sessions.theme import FONT_TYPES, lettering_css, stage_font_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings")


class ObsPatch(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = Field(None, min_length=1, max_length=200)
    port: Optional[int] = Field(None, ge=1, le=65535)
    password: Optional[str] = Field(None, max_length=200)  # written, never read back


class ProfilePatch(BaseModel):
    scene: Optional[str] = Field(None, max_length=200)
    zone: Optional[list[float]] = Field(None, min_length=4, max_length=4)
    clear_zone: bool = False  # "no camera zone"


class ReaderPatch(BaseModel):
    enabled: Optional[bool] = None


class AppearanceBody(BaseModel):
    appearance: Literal["system", "light", "dark"]  # src.config.APPEARANCES


class StageDefaultsPatch(BaseModel):
    theme: Optional[str] = Field(None, min_length=1, max_length=120)
    font: Optional[dict[str, Any]] = None  # a session.yaml ``font`` block; null = the theme's lettering


class MusicDefaultsPatch(BaseModel):
    fade_in_s: Optional[float] = Field(None, ge=0, le=defaults.MAX_FADE_S)
    fade_out_s: Optional[float] = Field(None, ge=0, le=defaults.MAX_FADE_S)


class DefaultsPatch(BaseModel):
    stage: Optional[StageDefaultsPatch] = None
    music: Optional[MusicDefaultsPatch] = None


class LibraryFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class SettingsPatch(BaseModel):
    obs: Optional[ObsPatch] = None
    profiles: Optional[dict[str, ProfilePatch]] = None
    reader: Optional[ReaderPatch] = None


def remote_payload(request: Request) -> dict[str, Any]:
    """Whether the phone remote is on, the app's base URL (the tailnet name over
    HTTPS once a cert is in place), and — for this PC only — the pairing link."""
    token = request.app.state.config.remote.token
    # Only a server that really speaks HTTPS gives out an https link (a dev or test instance may not).
    host = cert_hostname() if request.url.scheme == "https" else None
    port = (request.scope.get("server") or ("", request.app.state.config.port))[1]
    base = f"https://{host}:{port}" if host else f"{request.url.scheme}://127.0.0.1:{port}"
    link = f"{base}/remote?token={token}" if token and host and is_local(request.scope) else None
    return {"enabled": bool(token), "https": bool(host), "base_url": base, "link": link}


def payload(request: Request) -> dict[str, Any]:
    cfg = request.app.state.config
    obs = request.app.state.obs
    return {
        "remote": remote_payload(request),
        "obs": {"enabled": cfg.obs.enabled, "host": cfg.obs.host, "port": cfg.obs.port, "password_set": bool(cfg.obs.password)},
        "obs_state": obs.snapshot(),
        "scenes": list(obs.scenes),
        "profiles": profiles(cfg),
        "reader": {"enabled": cfg.reader.enabled, "window_title": cfg.reader.window_title, "poll_ms": cfg.reader.poll_ms},
        "stage_display": cfg.stage_display,
        "appearance": cfg.appearance,
        "source": cfg.source,
    }


@router.get("")
def get_settings(request: Request) -> dict[str, Any]:
    return payload(request)


@router.put("")
def put_settings(request: Request, body: SettingsPatch) -> dict[str, Any]:
    require_local(request, "Settings can only be changed on this PC")
    patch: dict[str, Any] = {}
    if body.obs:
        patch["obs"] = body.obs.model_dump(exclude_none=True)
    if body.profiles:
        known = profiles(request.app.state.config)
        out: dict[str, Any] = {}
        for key, p in body.profiles.items():
            if key not in known:
                raise AppError(404, "unknown_profile", f"No OBS profile {key!r}")
            entry: dict[str, Any] = {}
            if p.scene is not None:
                entry["scene"] = p.scene.strip()
            if p.clear_zone:
                entry["zone"] = None
            elif p.zone is not None:
                z = p.zone
                if not (0 <= z[0] < z[2] <= 1 and 0 <= z[1] < z[3] <= 1):
                    raise AppError(422, "bad_zone", "A camera zone is left, top, right, bottom as fractions (0–1), right > left, bottom > top")
                entry["zone"] = [round(v, 4) for v in z]
            out[key] = entry
        patch["profiles"] = out
    if body.reader:
        patch["reader"] = body.reader.model_dump(exclude_none=True)
    if patch:
        request.app.state.config = settings_file.update(patch)
        if "obs" in patch or "profiles" in patch:
            request.app.state.obs.reconnect()
        hub = request.app.state.live
        if "profiles" in patch and hub.session_id:
            hub.session_saved(hub.session_id)  # new zones on the stage
    return payload(request)


@router.get("/appearance")
def get_appearance(request: Request) -> dict[str, Any]:
    """The saved appearance. Open pages get the same value in the live snapshot (on connect and on
    every change); this is for a page that needs it without the socket."""
    return {"appearance": request.app.state.config.appearance}


@router.put("/appearance")
async def put_appearance(request: Request, body: AppearanceBody) -> dict[str, Any]:
    """Light, dark or the OS's choice for the app, the presenter and the phone remote, on every
    device at once. Not PC-only: the paired phone's own sun/moon button sets it too."""
    request.app.state.config = await asyncio.to_thread(settings_file.update, {"appearance": body.appearance})
    request.app.state.live.push_state()  # on the loop: every open page switches now
    return {"appearance": request.app.state.config.appearance}


@router.post("/obs/test")
async def test_obs(request: Request) -> dict[str, Any]:
    """Reconnect to OBS now and report what happened (within a few seconds)."""
    obs = request.app.state.obs
    before = obs.attempts
    obs.reconnect()
    for _ in range(60):  # the attempt itself times out after 3 s
        await asyncio.sleep(0.1)
        if obs.state == "off" or (obs.attempts > before and obs.state != "connecting"):
            break
    return payload(request)


@router.post("/remote/token")
def new_remote_token(request: Request) -> dict[str, Any]:
    """A fresh phone-remote token: turns the remote on, or unpairs every phone that had the old one."""
    require_local(request, "The phone link can only be made on this PC")
    request.app.state.config = settings_file.update({"remote": {"token": secrets.token_urlsafe(24)}})
    logger.info("✅ phone remote: new token (every earlier pairing is void)")
    return payload(request)


@router.delete("/remote/token")
def remote_off(request: Request) -> dict[str, Any]:
    """Turn the phone remote off: no other device gets in."""
    require_local(request, "The phone remote can only be switched off on this PC")
    request.app.state.config = settings_file.update({"remote": {"token": ""}})
    logger.info("ℹ️ phone remote switched off")
    return payload(request)


# ---------------------------------------------------------------- defaults (#110)

DEFAULT_FONT_URL = "/api/settings/defaults/font"
DEFAULT_FAMILY = "Default Font"


def defaults_payload(request: Request) -> dict[str, Any]:
    cfg = request.app.state.config
    return {**defaults.payload(defaults.load(cfg)), "library": library.payload(cfg)}


@router.get("/defaults")
def get_defaults(request: Request) -> dict[str, Any]:
    """What new sessions start from (stage theme + lettering, music fades) and the stage library."""
    return defaults_payload(request)


@router.put("/defaults")
def put_defaults(request: Request, body: DefaultsPatch) -> dict[str, Any]:
    """Change the defaults. Only new sessions take them; an existing session keeps its own look."""
    require_local(request, "Defaults can only be changed on this PC")
    cfg = request.app.state.config
    now = defaults.load(cfg)
    theme, font, fade_in, fade_out = now.theme, now.font, now.fade_in_s, now.fade_out_s
    if body.stage is not None:
        if body.stage.theme is not None:
            if not library.known_theme(cfg, body.stage.theme):
                raise AppError(422, "unknown_theme", f"No stage theme {body.stage.theme!r}")
            theme = body.stage.theme
        if "font" in body.stage.model_fields_set:
            try:
                font = defaults.normal_font(StageFont.model_validate(body.stage.font)) if body.stage.font else None
            except ValidationError as exc:
                raise AppError(422, "invalid_font", "The lettering is not valid", str(exc)) from exc
    if body.music is not None:
        fade_in = body.music.fade_in_s if body.music.fade_in_s is not None else fade_in
        fade_out = body.music.fade_out_s if body.music.fade_out_s is not None else fade_out
    new = defaults.Defaults(theme=theme, font=font, fade_in_s=fade_in, fade_out_s=fade_out)
    request.app.state.config = settings_file.update({"defaults": defaults.to_config(new)}, replace=("defaults",))
    return defaults_payload(request)


@router.post("/library/{kind}")
def add_to_library(request: Request, kind: Literal["font", "theme"], body: LibraryFile) -> dict[str, Any]:
    """Copy a font file or a stage theme (.css) on this PC into the stage library."""
    require_local(request, "The stage library can only be changed on this PC")
    added = library.add(request.app.state.config, kind, body.path)
    return {**defaults_payload(request), "added": {"name": added.name, "path": str(added)}}


@router.get("/defaults/font.css", include_in_schema=False)
def default_font_css(request: Request) -> Response:
    """The default lettering as CSS for Settings' own sample only (never a stage canvas)."""
    font = defaults.load(request.app.state.config).font
    css = lettering_css(font, DEFAULT_FONT_URL, scope=".stage-canvas.defaults-sample",
                       family=DEFAULT_FAMILY, complete=True)
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})


@router.get("/defaults/font", include_in_schema=False)
def default_font(request: Request) -> FileResponse:
    path = stage_font_file(defaults.load(request.app.state.config).font)
    if path is None:
        raise AppError(404, "font_not_found", "The default lettering has no font file on this PC")
    return FileResponse(path, media_type=FONT_TYPES[path.suffix.lower()], headers={"Cache-Control": "max-age=31536000, immutable"})


# ---------------------------------------------------------------- Spotify (#110)


def spotify_payload(request: Request, account: dict[str, Any]) -> dict[str, Any]:
    """The account for Settings → Music: states and names only — never the client id or a token."""
    return {**account, "client_id_set": bool(get_value(CLIENT_ID_KEY)), "logged_in": bool(get_value(REFRESH_KEY)),
            "login": request.app.state.spotify_login.snapshot()}


@router.get("/spotify")
async def get_spotify(request: Request) -> dict[str, Any]:
    """The Spotify account's state (asks Spotify at most every 30 s, in a worker thread)."""
    account = await asyncio.to_thread(request.app.state.music.spotify_account)
    return spotify_payload(request, account)


@router.post("/spotify/check")
async def check_spotify(request: Request) -> dict[str, Any]:
    """Ask Spotify now."""
    account = await asyncio.to_thread(request.app.state.music.spotify_account, fresh=True)
    return spotify_payload(request, account)


@router.post("/spotify/connect")
async def connect_spotify(request: Request) -> dict[str, Any]:
    """Start the Spotify login on this PC: the browser opens Spotify's consent page; the page
    polls GET /api/settings/spotify until ``login.state`` is ``done`` or ``failed``."""
    require_local(request, "Spotify can only be connected on this PC")
    try:
        await asyncio.to_thread(request.app.state.spotify_login.start)
    except LoginError as exc:
        raise AppError(409, "spotify_login", str(exc)) from exc
    account = {"state": "unknown", "detail": "Waiting for the Spotify login", "device": "", "checked_at": None}
    return spotify_payload(request, account)
