"""The podcast recorder's per-side tracks (content-management's ``recorder/``), found and read for one session.

The recorder holds a two-sided call, a **host** (the owner, ``me`` here) and a **guest** (``them``), and
leaves each side's take in the episode folder's ``video editing/`` as
``recorder - <side> - YYYY-MM-DD HHMMSS.mp4`` with a ``.json`` sidecar beside it: ``kind`` (``main`` for
a side's own camera and mic, ``ref`` for the other side as heard over the call), ``side``, ``file`` and
``duration_s``. Only ``main`` takes are read. A side that stopped and started again has several takes.

The time in the file name is when the recorder's server took the take's first chunk, in this PC's
local time to the second. The server runs on this PC, the clock ``live/events.jsonl`` uses, so a take
is placed on the session's clock from its name; the guest's take also carries its upload delay, so
the two sides line up to within a second or two (content-management's sync stage measures it exactly
for the podcast, from the ``ref`` files; this app does not repeat that).

A take's audio is read as 16 kHz mono PCM, the shape the chunker cuts: through ``ffmpeg`` for the
recorder's ``.mp4``, directly for a ``.wav`` (the synthetic fixtures).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Optional, cast

from src.copilot.audio import read_pcm
from src.copilot.vad import SAMPLE_BYTES, SAMPLE_RATE
from src.errors import DomainError
from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

VIDEO_DIR = "video editing"
SIDES = {"host": "me", "guest": "them"}
_NAME = re.compile(r"^recorder - (host|guest) - (\d{4}-\d{2}-\d{2} \d{6})\.(mp4|wav)$")
# One read from ffmpeg's pipe: two seconds of audio, so progress moves while memory stays flat.
READ_BYTES = SAMPLE_RATE * SAMPLE_BYTES * 2


class TrackError(DomainError):
    """A take that could not be read (``ffmpeg_missing``, ``unreadable``)."""


@dataclass(frozen=True)
class Track:
    """One side's take: ``started`` is the epoch second its first chunk reached the recorder."""

    path: Path
    side: str
    started: float
    duration_s: float

    @property
    def speaker(self) -> str:
        return SIDES[self.side]

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.path.name, "side": self.side, "speaker": self.speaker,
                "started_ms": int(self.started * 1000), "duration_s": self.duration_s}


def tracks_dir(folder: Path) -> Path:
    """Where the takes are: the episode folder's ``video editing/``, or the folder itself when it is that one."""
    inner = folder / VIDEO_DIR
    return inner if inner.is_dir() else folder


def _track(sidecar: Path) -> Optional[Track]:
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ recorder: %s unreadable (%s) — skipped", sidecar.name, exc)
        return None
    if not isinstance(meta, dict) or meta.get("kind") != "main":
        return None
    match = _NAME.match(str(meta.get("file") or ""))
    path = sidecar.with_name(str(meta.get("file") or ""))
    if match is None or match.group(1) != meta.get("side") or not path.is_file():
        return None
    try:
        duration = float(meta.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    started = datetime.strptime(match.group(2), "%Y-%m-%d %H%M%S").timestamp()  # this PC's local time
    return Track(path, match.group(1), started, duration)


def find_tracks(folder: Path, window: Optional[tuple[float, float]] = None) -> list[Track]:
    """The ``main`` takes in ``folder``, oldest first; with ``window`` (epoch s), only those overlapping it."""
    found = [t for t in (_track(p) for p in sorted(tracks_dir(folder).glob("recorder - *.json"))) if t is not None]
    if window is not None:
        start, end = window
        found = [t for t in found if t.started <= end and t.started + max(t.duration_s, 0.0) >= start]
    return sorted(found, key=lambda t: (t.started, t.side))


def live_window(events: list[dict[str, Any]]) -> Optional[tuple[float, float]]:
    """From the session's first going live to its last event, in epoch s; ``None`` when it never went live."""
    stamps: list[tuple[str, float]] = []
    for e in events:
        try:
            stamps.append((str(e.get("event") or ""), datetime.fromisoformat(str(e.get("at"))).timestamp()))
        except (TypeError, ValueError):
            continue
    went_live = [at for name, at in stamps if name == "session_live"]
    if not went_live:
        return None
    return min(went_live), max(at for _, at in stamps)


def pcm_blocks(track: Track) -> Iterator[bytes]:
    """The take's audio as 16 kHz mono 16-bit PCM, in blocks; ``TrackError`` when it cannot be read."""
    if track.path.suffix.lower() == ".wav":
        try:
            pcm = read_pcm(track.path)
        except (OSError, ValueError, EOFError) as exc:
            raise TrackError(422, "unreadable", f"{track.path.name} could not be read ({exc})") from exc
        for at in range(0, len(pcm), READ_BYTES):
            yield pcm[at:at + READ_BYTES]
        return
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise TrackError(409, "ffmpeg_missing", "ffmpeg is not on this PC's PATH: it reads the recorder's .mp4 tracks")
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", str(track.path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-f", "s16le", "-"]
    # -v error keeps stderr to a few lines, so it cannot fill its pipe while stdout is read.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=NO_WINDOW)
    out, errs = cast(IO[bytes], proc.stdout), cast(IO[bytes], proc.stderr)  # both piped above
    try:
        while block := out.read(READ_BYTES):
            yield block
        err = errs.read().decode("utf-8", "replace").strip()
        if proc.wait() != 0:
            raise TrackError(422, "unreadable", f"ffmpeg could not read {track.path.name}: {err[-300:] or 'no detail'}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        out.close()
        errs.close()
