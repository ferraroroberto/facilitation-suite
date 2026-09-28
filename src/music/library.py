"""A session's own audio: the files under ``<session>/audio/``.

A track picked in the Plan tab is **copied** into the session folder (the way
the roster import copies the ``.xlsx``), so the session stays self-contained:
duplicating or moving the folder takes its music along, and the offline check
covers it. ``session.yaml`` refers to it by its session-relative path
(``audio/warm-up.mp3``).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from src.music.backend import MusicError
from src.sessions.model import Session
from src.sessions.offline import OfflineReport

logger = logging.getLogger(__name__)

AUDIO_DIR = "audio"
# What miniaudio decodes (m4a/AAC is not among them — convert it to mp3 first).
AUDIO_TYPES: tuple[str, ...] = (".mp3", ".wav", ".ogg", ".flac")


def probe(path: Path) -> float:
    """The duration in seconds of a file the file backend can play; ``MusicError`` if it cannot."""
    if path.suffix.lower() not in AUDIO_TYPES:
        raise MusicError(422, "unsupported_audio", f"{path.name}: the app plays {', '.join(AUDIO_TYPES)} files")
    import miniaudio

    try:
        return float(miniaudio.get_file_info(str(path)).duration)
    except miniaudio.DecodeError as exc:
        raise MusicError(422, "unsupported_audio", f"{path.name} cannot be decoded ({exc})") from exc


def resolve(folder: Path, rel: str) -> Path:
    """A session-relative path inside the session folder (never outside it)."""
    root = folder.resolve()
    path = (root / rel).resolve()
    if not rel.strip() or not path.is_relative_to(root):
        raise MusicError(422, "bad_path", f"{rel!r} is not a file in the session folder")
    return path


def audio_files(folder: Path) -> list[str]:
    """Every playable file under ``audio/``, session-relative, by name."""
    base = folder / AUDIO_DIR
    if not base.is_dir():
        return []
    return sorted((p.relative_to(folder).as_posix() for p in base.rglob("*")
                   if p.is_file() and p.suffix.lower() in AUDIO_TYPES), key=str.lower)


def cue_file(folder: Path, name: str) -> Optional[Path]:
    """``audio/<name>.<ext>`` in the session folder (a sound cue, e.g. ``quiz-lobby.mp3``), else ``None``.

    Extensions are tried in ``AUDIO_TYPES`` order; the name matches without regard to case."""
    base = folder / AUDIO_DIR
    if not base.is_dir():
        return None
    found = {(p.stem.lower(), p.suffix.lower()): p for p in base.iterdir() if p.is_file()}
    return next((found[(name.lower(), ext)] for ext in AUDIO_TYPES if (name.lower(), ext) in found), None)


def import_audio(folder: Path, source: Path) -> dict[str, Any]:
    """Copy ``source`` into ``audio/`` (checked first) and return its session-relative path.

    The same file picked again is reused; a different file with the same name
    gets ``-2``, ``-3``… so an earlier item's track is never replaced."""
    if not source.is_file():
        raise MusicError(404, "not_found", "That file does not exist")
    duration = probe(source)
    target_dir = folder / AUDIO_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    n = 1
    while target.exists() and not _same(source, target):
        n += 1
        target = target_dir / f"{source.stem}-{n}{source.suffix}"
    if not target.exists():
        tmp = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, tmp)
        tmp.replace(target)
        logger.info("ℹ️ music: %s copied into the session as %s", source.name, target.relative_to(folder).as_posix())
    return {"path": target.relative_to(folder).as_posix(), "name": target.name, "duration_s": round(duration, 1)}


def _same(a: Path, b: Path) -> bool:
    if a.resolve() == b.resolve():
        return True
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def readiness(folder: Path, session: Session, offline: OfflineReport) -> Optional[dict[str, Any]]:
    """The "Music files" check — only for a session whose items play files.

    ``ok`` = every file is in the session folder, on this PC and decodable;
    ``warn`` names what is missing, cloud-only or unplayable; ``unknown`` when
    a file could not be checked."""
    paths = sorted({it.music.path for it in session.all_items()
                    if it.include and it.music and it.music.enabled and it.music.source == "file"})
    if not paths:
        return None
    cloud = set(offline.cloud_only)
    missing, in_cloud, bad, unchecked = [], [], [], []
    for rel in paths:
        try:
            path = resolve(folder, rel)
        except MusicError:
            bad.append(rel or "(no file)")
            continue
        if not path.is_file():
            missing.append(rel)
        elif rel in cloud:
            in_cloud.append(rel)  # reading it now would download it: the offline check says so
        else:
            try:
                probe(path)
            except MusicError:
                bad.append(rel)
            except (OSError, ImportError) as exc:
                logger.warning("⚠️ music: %s could not be checked (%s)", rel, exc)
                unchecked.append(rel)
    check = {"key": "music", "label": "Music files"}
    if missing or bad or in_cloud:
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + \
                ([f"only in the cloud: {', '.join(in_cloud)}"] if in_cloud else []) + \
                ([f"cannot be played: {', '.join(bad)}"] if bad else [])
        return dict(check, state="warn", detail="; ".join(parts))
    if unchecked:
        return dict(check, state="unknown", detail=f"{len(unchecked)} file(s) could not be checked")
    return dict(check, state="ok", detail=f"{len(paths)} track{'s' if len(paths) != 1 else ''} in audio/, on this PC")


def uses_spotify(session: Session) -> bool:
    return any(it.include and it.music and it.music.enabled and it.music.source == "spotify" for it in session.all_items())


SPOTIFY_STATES = {"ok": "ok", "unknown": "unknown"}  # every other state (not set up, expired, no device, not Premium) needs you


def spotify_check(status: Optional[tuple[str, str]]) -> dict[str, Any]:
    """The "Spotify" check — only for a session that plays Spotify: the token works and the
    desktop app on this PC is visible. ``unknown`` when it could not be checked, never ok."""
    if status is None:
        return {"key": "spotify", "label": "Spotify", "state": "unknown", "detail": "Could not be checked"}
    state, detail = status
    return {"key": "spotify", "label": "Spotify", "state": SPOTIFY_STATES.get(state, "warn"), "detail": detail}
