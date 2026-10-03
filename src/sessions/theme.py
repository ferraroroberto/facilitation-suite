"""A session's stage look on top of ``themes/default.css``: its lettering
(session.yaml → ``font``: the title font — file or installed family, weight,
line thickness, capitals —, the text font, and which of the two each kind of
text uses, with its capitals) as generated CSS, then its own ``theme.css``
from the folder. A session naming a library theme (``library/<name>``,
``src/library.py``) gets that theme's CSS first.

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

from src.sessions.model import Session, StageFont, TextRole

logger = logging.getLogger(__name__)

FONT_TYPES = {".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2"}
FAMILY = "Session Font"
FALLBACK = '"Patrick Hand", system-ui, sans-serif'
# A kind of text → the name its CSS variables carry.
CSS_ROLE = {"title": "title", "sub": "sub", "hint": "hint", "answers": "answers", "slide_text": "slide"}


def font_file(session: Session) -> Optional[Path]:
    """The session's font file when it is set, a font, and on this PC; else ``None``."""
    return stage_font_file(session.font)


def stage_font_file(font: Optional[StageFont]) -> Optional[Path]:
    """A lettering block's font file when it is set, a font, and on this PC; else ``None``."""
    name = (font.file if font else "").strip()
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
    return lettering_css(session.font, font_url)


def lettering_css(font: Optional[StageFont], font_url: str, *, scope: str = ".stage-canvas", family: str = FAMILY,
                  complete: bool = False) -> str:
    """``@font-face`` and the stage variables for one lettering block on ``scope``: the
    session's on every stage canvas; Settings' default lettering on its own sample only —
    ``complete`` then states every variable, so a session's lettering on the same page
    never shows through."""
    if font is None:
        if not complete:
            return ""
        font = StageFont()
    rules: list[str] = []
    props: list[str] = []
    path = stage_font_file(font)
    if path is not None:
        fmt = {".otf": "opentype", ".ttf": "truetype", ".woff": "woff", ".woff2": "woff2"}[path.suffix.lower()]
        version = path.stat().st_mtime_ns
        rules.append(f'@font-face {{ font-family: "{family}"; src: url("{font_url}?v={version}") format("{fmt}"); '
                     "font-display: block; }")
        props.append(f'--st-font: "{family}", {FALLBACK};')
    elif font.family.strip() and _family(font.family) != '""':
        props.append(f"--st-font: {_family(font.family)}, {FALLBACK};")
    elif complete:
        props.append(f"--st-font: {FALLBACK};")
    if font.weight != 400 or complete:
        props.append(f"--st-font-weight: {font.weight};")
    if font.stroke_px or complete:
        props.append(f"--st-font-stroke: {font.stroke_px:g}px;")
    if not font.caps or complete:
        props.append(f"--st-question-transform: {'uppercase' if font.caps else 'none'};")
    if font.title_color or complete:
        props.append(f"--st-title-color: {font.title_color or 'var(--st-ink)'};")
    if font.title_size or complete:
        props.append(f"--st-title-size: {font.title_size or 72}px;")
    if font.text_family.strip() and _family(font.text_family) != '""':
        props.append(f"--st-text-font: {_family(font.text_family)}, var(--st-ui-font);")
    elif complete:
        props.append("--st-text-font: var(--st-ui-font);")
    if font.text_weight != 400 or complete:
        props.append(f"--st-text-weight: {font.text_weight};")
    if complete:  # every kind of text, its default where the block says nothing
        for role in CSS_ROLE:
            own = font.roles.get(role) or TextRole()
            style = TextRole(font=own.font.strip() or ("title" if role == "title" else "text"),
                             caps=own.caps if own.caps is not None else (None if role == "title" else False))
            props += role_vars(role, style)
        props.append("--st-title-case: var(--st-question-transform);")
    else:
        for role, style in font.roles.items():
            props += role_vars(role, style)
    if props:
        rules.append(scope + " { " + " ".join(props) + " }")
    return "\n".join(rules)


def theme_css(session: Session, folder: Path, font_url: str, library_css: str = "") -> str:
    """The session's library theme (when it names one — ``src/library.py``), its font CSS,
    then the folder's own ``theme.css`` (which wins)."""
    parts = [library_css, font_css(session, font_url)]
    own = folder / "theme.css"
    if own.is_file():
        try:
            parts.append(own.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("⚠️ session theme.css unreadable: %s", exc)
    return "\n".join(p for p in parts if p)
