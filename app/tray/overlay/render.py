"""What the overlay draws, as Pillow images: the hint card and the cue icon.

Pure functions of the hints and the style (``copilot.overlay`` in the config), so the window only
blits what this returns, and a test or a screenshot for review renders the same pixels with no
screen at all. Every size is in the monitor's own pixels: ``scale`` is its DPI over 96.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from src.config import OVERLAY_SHAPES, OverlayConfig

logger = logging.getLogger(__name__)

# The families the Settings card offers, and their files in Windows' font folder.
FONT_FILES = {
    "Segoe UI": "segoeui.ttf", "Segoe UI Semibold": "seguisb.ttf", "Segoe UI Bold": "segoeuib.ttf",
    "Arial": "arial.ttf", "Calibri": "calibri.ttf", "Verdana": "verdana.ttf", "Tahoma": "tahoma.ttf",
    "Georgia": "georgia.ttf", "Consolas": "consola.ttf",
}
# One accent per hint type, readable on a dark or a light card.
TYPE_ACCENT = {"next_q": "#4ea1ff", "notice": "#f5b942", "drift": "#ff6b6b", "recap": "#4cc38a"}
TYPE_LABEL = {"next_q": "ASK", "notice": "NOTICE", "drift": "TIME", "recap": "RECAP"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULTS = OverlayConfig()


def _colour(value: Any, fallback: str) -> tuple[int, int, int]:
    text = value if isinstance(value, str) and _HEX.match(value) else fallback
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class Style:
    shape: str
    width: int
    font: str
    font_size: int
    text: tuple[int, int, int]
    background: tuple[int, int, int]
    opacity: int  # percent

    @classmethod
    def from_settings(cls, raw: dict[str, Any], scale: float = 1.0) -> Style:
        """The style from the snapshot's ``copilot.overlay``: a value out of range falls back."""
        return cls(
            shape=raw.get("shape") if raw.get("shape") in OVERLAY_SHAPES else DEFAULTS.shape,
            width=round(_clamp(raw.get("width"), 240, 2400, DEFAULTS.width) * scale),
            font=raw.get("font") if raw.get("font") in FONT_FILES else DEFAULTS.font,
            font_size=round(_clamp(raw.get("font_size"), 14, 96, DEFAULTS.font_size) * scale),
            text=_colour(raw.get("text_color"), DEFAULTS.text_color),
            background=_colour(raw.get("background"), DEFAULTS.background),
            opacity=_clamp(raw.get("opacity"), 20, 100, DEFAULTS.opacity),
        )


@lru_cache(maxsize=32)
def _font(family: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    folder = Path(os.environ.get("WINDIR") or "") / "Fonts"
    try:
        return ImageFont.truetype(str(folder / FONT_FILES.get(family, FONT_FILES[DEFAULTS.font])), size)
    except OSError:
        logger.warning("⚠️ overlay: font %r not found in %s — Pillow's own font instead", family, folder)
        return ImageFont.load_default(size=size)


def _wrap(text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and font.getlength(candidate) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return [*lines, line] if line else lines


def _radius(shape: str, height: int, size: int) -> int:
    return {"square": 0, "pill": height // 2}.get(shape, round(size * 0.7))


def render_hints(hints: list[dict[str, str]], style: Style) -> Image.Image:
    """The card: one row per hint, its type as a small coloured label above the words."""
    size = style.font_size
    pad = round(size * (1.1 if style.shape == "pill" else 0.75))
    gap = round(size * 0.5)
    font, label_font = _font(style.font, size), _font("Segoe UI Bold", max(10, round(size * 0.42)))
    inner = style.width - 2 * pad
    rows = [(h.get("type", "notice"), _wrap(h.get("text", ""), font, inner)) for h in hints] or [("notice", [""])]
    line_h, label_h = round(size * 1.25), round(size * 0.62)
    edge = round(size * 0.75)  # top and bottom: as much air as the sides of a rounded card
    height = 2 * edge + sum(label_h + line_h * len(lines) for _, lines in rows) + gap * (len(rows) - 1)
    img = Image.new("RGBA", (style.width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, style.width - 1, height - 1), _radius(style.shape, height, size),
                           fill=(*style.background, round(style.opacity * 2.55)))
    y = edge
    for kind, lines in rows:
        accent = _colour(TYPE_ACCENT.get(kind), TYPE_ACCENT["notice"])
        draw.text((pad, y), TYPE_LABEL.get(kind, kind.upper()), font=label_font, fill=(*accent, 255))
        y += label_h
        for line in lines:
            draw.text((pad, y), line, font=font, fill=(*style.text, 255))
            y += line_h
        y += gap
    return img


def render_cue(style: Style) -> Image.Image:
    """The cue: a small round badge with a notes glyph — "look at the presenter's notes"."""
    d = round(style.font_size * 2.2)
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ring = max(2, d // 14)
    accent = _colour(TYPE_ACCENT["next_q"], TYPE_ACCENT["next_q"])
    draw.ellipse((0, 0, d - 1, d - 1), fill=(*accent, 255))
    draw.ellipse((ring, ring, d - 1 - ring, d - 1 - ring), fill=(*style.background, max(200, round(style.opacity * 2.55))))
    bar, left, right = max(2, d // 14), round(d * 0.3), round(d * 0.7)
    for n, frac in enumerate((0.36, 0.5, 0.64)):
        y = round(d * frac)
        draw.rounded_rectangle((left, y - bar // 2, right - (round(d * 0.12) if n == 2 else 0), y + bar // 2), bar // 2,
                               fill=(*style.text, 255))
    return img


def premultiplied_bgra(img: Image.Image) -> bytes:
    """The pixels as Windows' ``UpdateLayeredWindow`` wants them: BGRA, alpha premultiplied."""
    return img.convert("RGBa").tobytes("raw", "BGRa")


def sample_hints() -> list[dict[str, str]]:
    """What Settings' "Show a sample" puts on the overlay."""
    return [{"type": "next_q", "text": "Ask what they would test first"},
            {"type": "drift", "text": "Four minutes behind: skip the second example"}]


def frame(hints: list[dict[str, str]], mode: str, raw_style: dict[str, Any], scale: float = 1.0) -> Optional[Image.Image]:
    """The image a pop-up shows: the card in ``full`` mode, the cue in ``cue`` mode (None: nothing to show)."""
    if not hints:
        return None
    style = Style.from_settings(raw_style, scale)
    return render_cue(style) if mode == "cue" else render_hints(hints, style)
