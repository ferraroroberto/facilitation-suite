"""Settings shared by every session: OBS connection and profiles, the chat reader."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.webapp.errors import AppError, require_local
from src import settings as settings_file
from src.config import profiles

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


class SettingsPatch(BaseModel):
    obs: Optional[ObsPatch] = None
    profiles: Optional[dict[str, ProfilePatch]] = None
    reader: Optional[ReaderPatch] = None


def payload(request: Request) -> dict[str, Any]:
    cfg = request.app.state.config
    obs = request.app.state.obs
    return {
        "obs": {"enabled": cfg.obs.enabled, "host": cfg.obs.host, "port": cfg.obs.port, "password_set": bool(cfg.obs.password)},
        "obs_state": obs.snapshot(),
        "scenes": list(obs.scenes),
        "profiles": profiles(cfg),
        "reader": {"enabled": cfg.reader.enabled, "window_title": cfg.reader.window_title, "poll_ms": cfg.reader.poll_ms},
        "stage_display": cfg.stage_display,
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
