"""Global defaults every new session starts from (#110): the stage theme and
lettering, and the fades of new music items.

They live in ``config/config.json`` under ``defaults``::

    "defaults": {
      "stage": {"theme": "default", "font": { ...a session.yaml ``font`` block... }},
      "music": {"fade_in_s": 2.0, "fade_out_s": 2.0}
    }

A new session **copies** the stage look into its own ``session.yaml``; after
that the session owns it. Changing a default never touches an existing
session — "Reset to default" on a session (``apply``) copies the current
defaults in again. The stage keeps following the session's own look.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError

from src.config import AppConfig
from src.sessions.model import Session, StageFont

logger = logging.getLogger(__name__)

DEFAULT_THEME = "default"
DEFAULT_FADE_S = 2.0
MAX_FADE_S = 60.0


@dataclass(frozen=True)
class Defaults:
    theme: str = DEFAULT_THEME
    font: Optional[StageFont] = None  # None = the theme's own lettering
    fade_in_s: float = DEFAULT_FADE_S
    fade_out_s: float = DEFAULT_FADE_S


def _fade(value: Any, key: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= MAX_FADE_S:
        return float(value)
    if value is not None:
        logger.warning("⚠️ config: defaults.music.%s %r is not 0–%g seconds — using %g", key, value, MAX_FADE_S, DEFAULT_FADE_S)
    return DEFAULT_FADE_S


def load(cfg: AppConfig) -> Defaults:
    """The defaults from the config; a bad value falls back to its own default (logged)."""
    raw = cfg.defaults if isinstance(cfg.defaults, dict) else {}
    stage = raw.get("stage") if isinstance(raw.get("stage"), dict) else {}
    music = raw.get("music") if isinstance(raw.get("music"), dict) else {}
    theme = stage.get("theme")
    if not isinstance(theme, str) or not theme.strip():
        theme = DEFAULT_THEME
    font: Optional[StageFont] = None
    if isinstance(stage.get("font"), dict):
        try:
            font = normal_font(StageFont.model_validate(stage["font"]))
        except ValidationError as exc:
            logger.warning("⚠️ config: defaults.stage.font is not valid (%s) — using the theme's lettering",
                           exc.errors()[0].get("msg"))
    return Defaults(theme=theme.strip(), font=font,
                    fade_in_s=_fade(music.get("fade_in_s"), "fade_in_s"), fade_out_s=_fade(music.get("fade_out_s"), "fade_out_s"))


def _font_data(font: Optional[StageFont]) -> dict[str, Any]:
    """A lettering block as comparable data: roles that only follow are dropped."""
    data = (font or StageFont()).model_dump(mode="json", exclude_none=True)
    roles = {k: r for k, r in (data.get("roles") or {}).items() if r.get("font") or r.get("caps") is not None}
    data["roles"] = {k: {kk: vv for kk, vv in r.items() if not (kk == "font" and vv == "")} for k, r in roles.items()}
    return data


def normal_font(font: Optional[StageFont]) -> Optional[StageFont]:
    """``None`` for a block that says nothing the theme doesn't (all defaults)."""
    if font is None or _font_data(font) == _font_data(None):
        return None
    return StageFont.model_validate(_font_data(font))


def to_config(d: Defaults) -> dict[str, Any]:
    """The ``defaults`` object written to the config file."""
    stage: dict[str, Any] = {"theme": d.theme}
    if d.font is not None:
        stage["font"] = _font_data(d.font)
    return {"stage": stage, "music": {"fade_in_s": d.fade_in_s, "fade_out_s": d.fade_out_s}}


def payload(d: Defaults) -> dict[str, Any]:
    return {"stage": {"theme": d.theme, "font": _font_data(d.font) if d.font else None},
            "music": {"fade_in_s": d.fade_in_s, "fade_out_s": d.fade_out_s}}


def uses_defaults(session: Session, d: Defaults) -> bool:
    """The session's stage look is the current defaults' (theme and lettering)."""
    return session.theme == d.theme and _font_data(session.font) == _font_data(d.font)


def apply(session: Session, d: Defaults) -> Session:
    """The session's stage look set to the defaults (a copy — the session owns it afterwards).
    Items keep their own exceptions."""
    session.theme = d.theme
    session.font = d.font.model_copy(deep=True) if d.font is not None else None
    return session
