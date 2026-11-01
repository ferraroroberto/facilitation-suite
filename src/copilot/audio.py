"""WAV in and out: a chunk as an in-memory WAV for the hub, and each channel kept on disk as it arrives."""

from __future__ import annotations

import io
import logging
import struct
import wave
from collections.abc import Callable
from pathlib import Path

from src.copilot.vad import SAMPLE_BYTES, SAMPLE_RATE

logger = logging.getLogger(__name__)

HEADER_BYTES = 44
# A channel reaches disk every 2 s: often enough that a crash loses little, rarely enough that
# OneDrive is not handed a new file version ten times a second.
FLUSH_BYTES = SAMPLE_RATE * SAMPLE_BYTES * 2
# While the file cannot be written (OneDrive holding it), up to 10 minutes wait in memory.
MAX_PENDING_BYTES = SAMPLE_RATE * SAMPLE_BYTES * 600


def wav_header(data_bytes: int) -> bytes:
    """The 44-byte header of a 16 kHz mono 16-bit PCM WAV holding ``data_bytes`` of samples."""
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * SAMPLE_BYTES, SAMPLE_BYTES, 8 * SAMPLE_BYTES)
            + b"data" + struct.pack("<I", data_bytes))


def wav_bytes(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit PCM wrapped as a WAV file in memory (what the transcription upload carries)."""
    return wav_header(len(pcm)) + pcm


def read_pcm(path: Path) -> bytes:
    """A 16 kHz mono 16-bit WAV's samples; anything else is refused (the feed and the tests write that shape)."""
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, SAMPLE_BYTES, SAMPLE_RATE):
            raise ValueError(f"{path.name} is not 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def write_wav(path: Path, pcm: bytes) -> None:
    path.write_bytes(wav_bytes(pcm))


class WavRecorder:
    """One channel appended to a WAV file as it arrives, the header's sizes patched on every flush so
    the file stays playable whenever the server stops. The file is opened only while a flush runs:
    an open handle would keep the presenter's "start over" from setting the ``live/`` folder aside
    (a file that vanished with it starts again, header first). A failed flush keeps its samples for
    the next one and is reported once through ``failed``; ``written`` hears when it clears."""

    def __init__(self, path: Path, failed: Callable[[str, OSError], None], written: Callable[[str], object]) -> None:
        self.path = path
        self.failed, self.written = failed, written
        self._pending = bytearray()
        self._failing = False

    def write(self, pcm: bytes) -> None:
        self._pending += pcm
        if len(self._pending) >= FLUSH_BYTES:
            self.flush()

    def flush(self) -> bool:
        if not self._pending:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists()
            with open(self.path, "w+b" if fresh else "r+b") as fh:
                if fresh:
                    fh.write(wav_header(0))
                fh.seek(0, io.SEEK_END)
                fh.write(self._pending)
                data = fh.tell() - HEADER_BYTES
                fh.seek(4)
                fh.write(struct.pack("<I", 36 + data))
                fh.seek(40)
                fh.write(struct.pack("<I", data))
        except OSError as exc:
            if not self._failing:
                self._failing = True
                self.failed(self.path.name, exc)
            if len(self._pending) > MAX_PENDING_BYTES:
                del self._pending[:len(self._pending) - MAX_PENDING_BYTES]
            return False
        self._pending.clear()
        if self._failing:
            self._failing = False
            self.written(self.path.name)
        return True

    def close(self) -> None:
        if not self.flush():
            logger.warning("⚠️ copilot: %d s of %s never reached disk", len(self._pending) // (SAMPLE_RATE * SAMPLE_BYTES),
                           self.path.name)
