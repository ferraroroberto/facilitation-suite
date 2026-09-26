"""A session's stage look on top of ``themes/default.css``: its lettering
(session.yaml → ``font``: the title font — file or installed family, weight,
line thickness, capitals —, the text font, and which of the two each kind of
text uses, with its capitals) as generated CSS, then its own ``theme.css``
from the folder.

Each kind of text (``TEXT_ROLES``) reads four variables — ``--st-<role>-font``,
``-weight``, ``-stroke`` and ``-case`` — whose defaults are in
``themes/default.css``; the stage sets the same variables on one item for its
own exceptions (``stage-render.js`` → ``roleStyle``, the same mapping).

The font file stays where it is on this PC (it is named by path in
session.yaml) and is served by ``/api/sessions/{sid}/font``; the URL carries
its modification time so a replaced file is fetched again.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from src.sessions.model import Session, TextRole

logger = logging.getLogger(__name__)

FONT_TYPES = {".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2"}
FAMILY = "Session Font"
FALLBACK = '"Patrick Hand", system-ui, sans-serif'
# A kind of text → the name its CSS variables carry.
CSS_ROLE = {"title": "title", "sub": "sub", "hint": "hint", "answers": "answers", "slide_text": "slide"}


def font_file(session: Session) -> Optional[Path]:
    """The session's font file when it is set, a font, and on this PC; else ``None``."""
    name = (session.font.file if session.font else "").strip()
    if not name:
        return None
    path = Path(name)
    if path.suffix.lower() not in FONT_TYPES:
        logger.warning("⚠️ stage font is not a font file: %s", path.name)
        return None
    if not path.is_file():
        logger.warning("⚠️ stage font not found: %s", path)
        return None
    return path


def _family(name: str) -> str:
    """An installed font's name as a CSS string (quotes and escapes dropped)."""
    return '"' + re.sub(r'["\\;{}<>]', "", name).strip() + '"'


def role_vars(role: str, style: TextRole) -> list[str]:
    """The CSS variables that give one kind of text its font and capitals."""
    r = f"--st-{CSS_ROLE[role]}"
    out: list[str] = []
    font = style.font.strip()
    if font == "title":
        out += [f"{r}-font: var(--st-font);", f"{r}-weight: var(--st-font-weight);", f"{r}-stroke: var(--st-font-stroke);"]
    elif font and (font == "text" or _family(font) != '""'):
        family = "var(--st-text-font)" if font == "text" else f"{_family(font)}, var(--st-text-font)"
        # the chat hint is a bold chip in the text font
        weight = "max(600, var(--st-text-weight))" if role == "hint" else "var(--st-text-weight)"
        out += [f"{r}-font: {family};", f"{r}-weight: {weight};", f"{r}-stroke: 0px;"]
    if style.caps is not None and role != "title":  # a title's capitals are font.caps
        out.append(f"{r}-case: {'uppercase' if style.caps else 'none'};")
    return out


def font_css(session: Session, font_url: str) -> str:
    """``@font-face`` and the stage variables for the session's lettering (empty when it has none)."""
    font = session.font
    if font is None:
        return ""
    rules: list[str] = []
    props: list[str] = []
    path = font_file(session)
    if path is not None:
        fmt = {".otf": "opentype", ".ttf": "truetype", ".woff": "woff", ".woff2": "woff2"}[path.suffix.lower()]
        version = path.stat().st_mtime_ns
        rules.append(f'@font-face {{ font-family: "{FAMILY}"; src: url("{font_url}?v={version}") format("{fmt}"); '
                     "font-display: block; }")
        props.append(f'--st-font: "{FAMILY}", {FALLBACK};')
    elif font.family.strip() and _family(font.family) != '""':
        props.append(f"--st-font: {_family(font.family)}, {FALLBACK};")
    if font.weight != 400:
        props.append(f"--st-font-weight: {font.weight};")
    if font.stroke_px:
        props.append(f"--st-font-stroke: {font.stroke_px:g}px;")
    if not font.caps:
        props.append("--st-question-transform: none;")
    if font.text_family.strip() and _family(font.text_family) != '""':
        props.append(f"--st-text-font: {_family(font.text_family)}, var(--st-ui-font);")
    if font.text_weight != 400:
        props.append(f"--st-text-weight: {font.text_weight};")
    for role, style in font.roles.items():
        props += role_vars(role, style)
    if props:
        rules.append(".stage-canvas { " + " ".join(props) + " }")
    return "\n".join(rules)


def theme_css(session: Session, folder: Path, font_url: str) -> str:
    """The session's font CSS followed by the folder's own ``theme.css`` (which wins)."""
    parts = [font_css(session, font_url)]
    own = folder / "theme.css"
    if own.is_file():
        try:
            parts.append(own.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("⚠️ session theme.css unreadable: %s", exc)
    return "\n".join(p for p in parts if p)
