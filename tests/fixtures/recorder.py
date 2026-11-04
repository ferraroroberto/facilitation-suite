"""A synthetic podcast-recorder episode: per-side takes named and sidecar'd the way content-management's
recorder leaves them in ``video editing/``, with tone bursts for voices. No recording of anyone.

The recorder writes ``.mp4``; the fixtures write the same names as ``.wav`` (16 kHz mono), which the
after-call pass reads directly, so the unit tests need no ffmpeg.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.copilot.audio import wav_bytes
from tests.copilot.synth import speech

# Ten seconds a side: under a chunk's minimum, so each take is sent to the hub as one chunk.
TAKE = [("voice", 2.0), ("pause", 1.0), ("voice", 3.0), ("pause", 1.0), ("voice", 3.0)]


def take_name(side: str, started: float, *, ref: bool = False, ext: str = "wav") -> str:
    stamp = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H%M%S")  # local time, as the recorder names it
    return f"recorder - {side}{' remote-ref' if ref else ''} - {stamp}.{ext}"


def write_take(episode: Path, side: str, started: float, *, ref: bool = False, pattern: list | None = None) -> Path:
    """One take and its sidecar under ``episode/video editing/``; a ``ref`` take points at no real main."""
    folder = episode / "video editing"
    folder.mkdir(parents=True, exist_ok=True)
    pcm = speech(pattern or TAKE)
    path = folder / take_name(side, started, ref=ref)
    path.write_bytes(wav_bytes(pcm))
    sidecar = {"kind": "ref" if ref else "main", "side": side, "rid": f"rid-{side}-{int(started)}", "file": path.name,
               "duration_s": round(len(pcm) / 32000, 3)}
    if ref:
        sidecar.update(main="rid-other", offset_in_main_s=0.5)
    path.with_suffix(".json").write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    return path


def write_episode(episode: Path, started: float, *, guest_lag_s: float = 1.0) -> dict[str, Path]:
    """A host take at ``started`` and a guest take ``guest_lag_s`` later, plus each side's call reference."""
    return {"host": write_take(episode, "host", started), "guest": write_take(episode, "guest", started + guest_lag_s),
            "host_ref": write_take(episode, "host", started, ref=True),
            "guest_ref": write_take(episode, "guest", started + guest_lag_s, ref=True)}
