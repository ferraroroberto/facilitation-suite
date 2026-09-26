"""A session's stage look on top of ``themes/default.css``: its font (session.yaml
→ ``font``) as generated CSS, then its own ``theme.css`` from the folder.

The font file stays where it is on this PC (it is named by path in
session.yaml) and is served by ``/api/sessions/{sid}/font``; the URL carries
its modification time so a replaced file is fetched again.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.sessions.model import Session

logger = logging.getLogger(__name__)

FONT_TYPES = {".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2"}
FAMILY = "Session Font"
FALLBACK = '"Patrick Hand", system-ui, sans-serif'


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


def font_css(session: Session, font_url: str) -> str:
    """``@font-face`` and the stage variables for the session's font (empty when it has none)."""
    if session.font is None:
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
    if session.font.stroke_px:
        props.append(f"--st-font-stroke: {session.font.stroke_px:g}px;")
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
