"""Voice-activity chunking: one channel's 16 kHz mono PCM cut into chunks worth transcribing.

The gate is the RMS level of each 20 ms frame in dBFS (voice-transcriber's silence check, per
frame here): a frame under ``silence_dbfs`` is silence. A chunk is cut at the first run of
``silence_ms`` of silence once it is ``min_s`` long, and at ``max_s`` whatever is being said, so
the transcript trails the voice by at most a chunk. A chunk with almost no voice in it is dropped
before it reaches whisper, which invents plausible text ("Thanks for watching!") on near-silence.
"""

from __future__ import annotations

import math
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

SAMPLE_RATE = 16000
SAMPLE_BYTES = 2
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES
SILENT_FLOOR_DBFS = -120.0
# Less voice than this in a chunk (a click, a breath, a cough) is not worth a whisper call.
MIN_VOICED_MS = 300
FULL_SCALE_SQ = 32768.0 * 32768.0


def frame_dbfs(frame: bytes) -> float:
    """RMS level of 16-bit PCM in dBFS (``SILENT_FLOOR_DBFS`` for an empty or all-zero frame)."""
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % SAMPLE_BYTES])
    if not samples:
        return SILENT_FLOOR_DBFS
    mean_sq = math.sumprod(samples, samples) / len(samples)
    if mean_sq <= 0:
        return SILENT_FLOOR_DBFS
    return 10.0 * math.log10(mean_sq / FULL_SCALE_SQ)


@dataclass(frozen=True)
class Chunk:
    """One cut of one channel: ``t0``/``t1`` in seconds since the channel started, ``ended_at`` the
    wall clock (epoch seconds) when its last sample arrived — where the transcript lag is measured from."""

    speaker: str
    t0: float
    t1: float
    pcm: bytes
    ended_at: float


class Chunker:
    """Feed it PCM as it arrives; it returns the chunks that are complete."""

    def __init__(self, speaker: str, *, silence_dbfs: float = -50.0, min_s: float = 20.0, max_s: float = 45.0,
                 silence_ms: int = 600, clock: Callable[[], float] = time.time) -> None:
        self.speaker = speaker
        self.silence_dbfs = float(silence_dbfs)
        self.min_s, self.max_s = float(min_s), float(max(max_s, min_s))
        self.silence_frames = max(1, int(silence_ms) // FRAME_MS)
        self.clock = clock
        self._partial = bytearray()
        self._buf = bytearray()
        self._start_sample = 0
        self._consumed = 0
        self._silent_run = 0
        self._voiced = 0

    def feed(self, pcm: bytes) -> list[Chunk]:
        self._partial += pcm
        out: list[Chunk] = []
        whole = len(self._partial) - len(self._partial) % FRAME_BYTES
        for at in range(0, whole, FRAME_BYTES):
            chunk = self._frame(bytes(self._partial[at:at + FRAME_BYTES]))
            if chunk is not None:
                out.append(chunk)
        del self._partial[:whole]
        return out

    def flush(self) -> list[Chunk]:
        """What is left when the channel stops (the last words before Copilot is switched off)."""
        if self._partial:
            self._buf += self._partial
            self._consumed += len(self._partial) // SAMPLE_BYTES
            self._partial.clear()
        chunk = self._cut() if self._buf else None
        return [chunk] if chunk is not None else []

    def _frame(self, frame: bytes) -> Optional[Chunk]:
        self._buf += frame
        self._consumed += FRAME_SAMPLES
        if frame_dbfs(frame) >= self.silence_dbfs:
            self._voiced += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        seconds = len(self._buf) / SAMPLE_BYTES / SAMPLE_RATE
        if seconds >= self.max_s or (seconds >= self.min_s and self._silent_run >= self.silence_frames):
            return self._cut()
        return None

    def _cut(self) -> Optional[Chunk]:
        pcm, voiced = bytes(self._buf), self._voiced
        t0 = self._start_sample / SAMPLE_RATE
        self._buf.clear()
        self._start_sample = self._consumed
        self._silent_run = self._voiced = 0
        if voiced * FRAME_MS < MIN_VOICED_MS:
            return None
        return Chunk(self.speaker, round(t0, 3), round(t0 + len(pcm) / SAMPLE_BYTES / SAMPLE_RATE, 3), pcm, self.clock())
