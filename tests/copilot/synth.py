"""Synthetic audio for the copilot tests: tone bursts stand in for a voice, zeros and faint noise
for silence. 16 kHz mono 16-bit, the shape the pipeline works in. No recording of anyone."""

from __future__ import annotations

import math
import random
from array import array

RATE = 16000


def tone(seconds: float, dbfs: float = -20.0, hz: float = 220.0) -> bytes:
    amp = 32767 * 10 ** (dbfs / 20)
    return array("h", (int(amp * math.sin(2 * math.pi * hz * n / RATE)) for n in range(int(seconds * RATE)))).tobytes()


def silence(seconds: float, dbfs: float = -75.0) -> bytes:
    """Room-tone: faint noise well under the gate (all zeros when ``dbfs`` is None)."""
    amp = 32767 * 10 ** (dbfs / 20)
    rnd = random.Random(7)
    return array("h", (int(rnd.uniform(-amp, amp)) for _ in range(int(seconds * RATE)))).tobytes()


def speech(pattern: list[tuple[str, float]]) -> bytes:
    """``[("voice", 2.0), ("pause", 0.8), ...]`` as one PCM stream."""
    return b"".join(tone(s) if kind == "voice" else silence(s) for kind, s in pattern)
