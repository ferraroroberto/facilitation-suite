"""The live copilot: its overlay's settings and screens, the recordings' retention, the session's local-only
switch and mode (workshop or interview), the facilitator's consent confirmation, a sample on the overlay and an injected transcript (a test or a
rehearsal). Copilot on/off is the live action ``copilot_toggle`` (``src/live/actions.py``), so the presenter, the remote and a Stream Deck share it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.tray.overlay.render import FONT_FILES
from app.tray.overlay.screens import list_screens
from app.webapp.errors import require_local
from src import settings as settings_file
from src.config import OVERLAY_MODES, OVERLAY_POSITIONS, OVERLAY_SHAPES
from src.copilot.service import CopilotService
from src.sessions.model import INTERVIEWEE_CHARS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot")
COLOUR = r"^#[0-9a-fA-F]{6}$"


def _copilot(request: Request) -> CopilotService:
    return request.app.state.copilot


class OverlayPatch(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[Literal["full", "cue"]] = None  # src.config.OVERLAY_MODES
    screen: Optional[str] = Field(None, max_length=64)
    position: Optional[Literal["top-left", "top-center", "top-right", "center",
                               "bottom-left", "bottom-center", "bottom-right"]] = None  # OVERLAY_POSITIONS
    margin: Optional[int] = Field(None, ge=0, le=400)
    shape: Optional[Literal["rounded", "pill", "square"]] = None  # OVERLAY_SHAPES
    width: Optional[int] = Field(None, ge=240, le=2400)
    font: Optional[str] = Field(None, max_length=40)
    font_size: Optional[int] = Field(None, ge=14, le=96)
    text_color: Optional[str] = Field(None, pattern=COLOUR)
    background: Optional[str] = Field(None, pattern=COLOUR)
    opacity: Optional[int] = Field(None, ge=20, le=100)
    show_seconds: Optional[int] = Field(None, ge=2, le=120)


class SettingsBody(BaseModel):
    overlay: Optional[OverlayPatch] = None
    recording_retention_days: Optional[int] = Field(None, ge=0, le=3650)  # 0 = keep


class LocalOnly(BaseModel):
    local_only: bool


class Mode(BaseModel):
    mode: Literal["workshop", "interview"]  # src.sessions.model.COPILOT_MODES
    interviewee: Optional[str] = Field(None, max_length=INTERVIEWEE_CHARS)  # None keeps the saved name


class Spoken(BaseModel):
    speaker: Literal["me", "them"]
    text: str = Field(min_length=1, max_length=2000)


class Transcript(BaseModel):
    lines: list[Spoken] = Field(min_length=1, max_length=50)


def settings_payload(request: Request) -> dict[str, Any]:
    cfg = request.app.state.config.copilot
    return {
        "overlay": asdict(cfg.overlay),
        "recording_retention_days": cfg.recording_retention_days,
        "running": request.app.state.overlay.running(),
        "screens": [s.as_dict() for s in list_screens()],
        "fonts": list(FONT_FILES),
        "modes": list(OVERLAY_MODES), "positions": list(OVERLAY_POSITIONS), "shapes": list(OVERLAY_SHAPES),
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    """The overlay's settings, with the monitors this PC shows and the fonts it can use."""
    return settings_payload(request)


@router.put("/settings")
async def put_settings(request: Request, body: SettingsBody) -> dict[str, Any]:
    """Change the overlay's look or place (an open overlay follows at once and shows a sample there) or how
    long the recordings are kept."""
    require_local(request, "The overlay can only be set up on this PC")
    patch = body.overlay.model_dump(exclude_none=True) if body.overlay else {}
    if "font" in patch and patch["font"] not in FONT_FILES:
        patch.pop("font")
    if body.recording_retention_days is not None:
        request.app.state.config = await asyncio.to_thread(
            settings_file.update, {"copilot": {"recording_retention_days": body.recording_retention_days}})
        logger.info("ℹ️ copilot: recordings are %s", f"removed after {body.recording_retention_days} days"
                    if body.recording_retention_days else "kept")
    if patch:
        request.app.state.config = await asyncio.to_thread(settings_file.update, {"copilot": {"overlay": patch}})
        if request.app.state.config.copilot.overlay.enabled:
            _copilot(request).sample()
        else:
            await asyncio.to_thread(request.app.state.overlay.stop)
            request.app.state.live.push_state()
    return settings_payload(request)


@router.post("/sample")
async def show_sample(request: Request) -> dict[str, Any]:
    """Put two sample hints on the overlay (starting it when it is not running) to check where it sits."""
    require_local(request, "The overlay can only be tried on this PC")
    _copilot(request).sample()
    return settings_payload(request)


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    """The copilot block of the live snapshot, on its own: the presenter polls it for the channels' live level."""
    return _copilot(request).snapshot()


@router.post("/test")
async def post_test(request: Request) -> dict[str, Any]:
    """One hint check now on what the facilitator's own mic said, its hints labelled a test (a solo dry run)."""
    copilot = _copilot(request)
    copilot.test_hint()
    return copilot.snapshot()


@router.put("/local-only")
async def put_local_only(request: Request, body: LocalOnly) -> dict[str, Any]:
    """The live session's switch: hints from the local model only (saved in its ``session.yaml``)."""
    copilot = _copilot(request)
    copilot.set_local_only(body.local_only)
    return copilot.snapshot()


@router.put("/mode")
async def put_mode(request: Request, body: Mode) -> dict[str, Any]:
    """The live session's mode and its interviewee's name (saved in its ``session.yaml``); while Copilot is on
    the next hint is asked in the new shape."""
    copilot = _copilot(request)
    copilot.set_mode(body.mode, body.interviewee)
    return copilot.snapshot()


@router.post("/consent")
async def post_consent(request: Request) -> dict[str, Any]:
    """The facilitator confirms the participants were informed and consented to the recording: saved
    in the live session's ``session.yaml`` and ``live/events.jsonl``, asked once per session."""
    copilot = _copilot(request)
    copilot.confirm_consent()
    return copilot.snapshot()


@router.post("/transcript")
async def inject_transcript(request: Request, body: Transcript) -> dict[str, Any]:
    """Lines added to the transcript as if heard, then hints asked at once (this PC only)."""
    require_local(request, "A transcript can only be injected on this PC")
    added = _copilot(request).inject([(line.speaker, line.text) for line in body.lines])
    return {"added": added}

