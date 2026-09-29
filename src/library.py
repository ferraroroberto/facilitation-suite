"""The stage library: font files and stage themes the facilitator adds once and
every session can use (#110).

User files, never the repo: they live in ``<session_root>/_library/fonts`` and
``<session_root>/_library/themes`` — next to the session folders (OneDrive by
default), so they travel with the sessions that use them. Adding a file
copies it there; a file already there with the same bytes is reused, a
different one with the same name becomes ``<name>-2``.

Themes are named in ``session.yaml`` (``theme:``): a repo theme by its file
name (``themes/default.css`` → ``default``), a library theme as
``library/<name>``. A library theme's CSS is served inside the session's own
``theme.css`` (``src/sessions/theme.py``); like the repo themes, its rules are
scoped under ``.stage-canvas``.
"""

from __future__ import annotations

import filecmp
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, AppConfig, session_root
from src.errors import DomainError
from src.sessions.theme import FONT_TYPES

logger = logging.getLogger(__name__)

LIBRARY_DIR = "_library"
REPO_THEMES = PROJECT_ROOT / "themes"
THEME_PREFIX = "library/"
THEME_TYPES = {".css": "text/css"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")


class LibraryError(DomainError):
    """A file the library cannot take (wrong type, missing, not copyable)."""


def library_dir(cfg: AppConfig) -> Path:
    return session_root(cfg) / LIBRARY_DIR


def fonts_dir(cfg: AppConfig) -> Path:
    return library_dir(cfg) / "fonts"


def themes_dir(cfg: AppConfig) -> Path:
    return library_dir(cfg) / "themes"


def _files(folder: Path, types: dict[str, str]) -> list[Path]:
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in types), key=lambda p: p.name.lower())
    except OSError:
        return []  # no library yet


def fonts(cfg: AppConfig) -> list[dict[str, str]]:
    """The library's font files: ``name`` (the file name) and ``path`` (what ``font.file`` holds)."""
    return [{"name": p.name, "path": str(p)} for p in _files(fonts_dir(cfg), FONT_TYPES)]


def themes(cfg: AppConfig) -> list[dict[str, str]]:
    """Every stage theme a session can name: the repo's, then the library's."""
    out = [{"value": p.stem, "label": p.stem.replace("-", " ").capitalize(), "source": "repo"}
           for p in _files(REPO_THEMES, THEME_TYPES) if re.fullmatch(r"[a-z0-9-]+", p.stem)]
    out += [{"value": THEME_PREFIX + p.stem, "label": p.stem, "source": "library"}
            for p in _files(themes_dir(cfg), THEME_TYPES) if _NAME.fullmatch(p.stem)]
    return out


def known_theme(cfg: AppConfig, name: str) -> bool:
    return any(t["value"] == name for t in themes(cfg))


def library_theme_css(cfg: AppConfig, name: str) -> str:
    """A library theme's CSS (``library/<name>``); ``""`` for a repo theme or a missing file."""
    if not name.startswith(THEME_PREFIX):
        return ""
    stem = name[len(THEME_PREFIX):]
    if not _NAME.fullmatch(stem):
        return ""
    path = themes_dir(cfg) / f"{stem}.css"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("⚠️ library theme %s unreadable: %s", stem, exc)
        return ""


def add(cfg: AppConfig, kind: str, source: str) -> Path:
    """Copy a font (``kind="font"``) or a theme (``"theme"``) into the library; returns its path there."""
    types, folder = (FONT_TYPES, fonts_dir(cfg)) if kind == "font" else (THEME_TYPES, themes_dir(cfg))
    src = Path(source.strip().strip('"'))
    if src.suffix.lower() not in types:
        raise LibraryError(422, "wrong_type", f"Not a {'font file (' + ', '.join(FONT_TYPES) + ')' if kind == 'font' else 'stage theme (.css)'}")
    if not src.is_file():
        raise LibraryError(404, "file_not_found", "That file is not on this PC")
    stem = re.sub(r"[^A-Za-z0-9 ._-]+", "-", src.stem).strip(" .-") or kind
    try:
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while True:
            dest = folder / f"{stem}{'' if n == 1 else f'-{n}'}{src.suffix.lower()}"
            if not dest.exists():
                shutil.copy2(src, dest)
                logger.info("✅ library: added %s %s", kind, dest.name)
                return dest
            if dest.resolve() == src.resolve() or filecmp.cmp(src, dest, shallow=False):
                return dest  # already in the library
            n += 1
    except OSError as exc:
        logger.error("❌ library: could not copy %s into %s: %s", src.name, folder, exc)
        raise LibraryError(500, "library_write_failed", f"Could not copy the file into {folder}") from exc


def payload(cfg: AppConfig) -> dict[str, Any]:
    return {"path": str(library_dir(cfg)), "fonts": fonts(cfg), "themes": themes(cfg)}
