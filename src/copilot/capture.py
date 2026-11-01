"""Where the two channels come from: the facilitator's mic and the call's sound, or two WAV files.

``me`` is the default (or named) input device. ``them`` is a WASAPI loopback of the default (or
named) output: what Zoom or Teams plays, so the other participants. miniaudio's C core has loopback
(``ma_device_type_loopback``, WASAPI only), but its Python ``CaptureDevice`` always opens an input,
so ``_LoopbackDevice`` repeats that constructor with the loopback type. Both ask miniaudio for
16 kHz mono 16-bit, and miniaudio converts from whatever the device runs at.

``FileSource`` plays a WAV in at real-time pace: a dry run of the whole pipeline with no audio
device (``copilot.capture: files``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import miniaudio

from src.copilot.audio import read_pcm
from src.copilot.vad import SAMPLE_BYTES, SAMPLE_RATE

logger = logging.getLogger(__name__)

PcmSink = Callable[[str, bytes], None]
BLOCK_MS = 100
KIND_LABEL = {"mic": "Mic", "loopback": "Call audio"}


class CaptureError(Exception):
    """A channel could not be opened (no such device, no audio device at all, a busy device)."""


def _device_id(kind: str, fragment: str) -> Any:
    """The miniaudio id of the device whose name holds ``fragment`` (None: Windows' default)."""
    if not fragment.strip():
        return None
    devices = miniaudio.Devices(backends=[miniaudio.Backend.WASAPI])
    pool = devices.get_captures() if kind == "mic" else devices.get_playbacks()
    for dev in pool:
        if fragment.strip().lower() in dev["name"].lower():
            return dev["id"]
    names = ", ".join(d["name"] for d in pool) or "none"
    raise CaptureError(f"no {'input' if kind == 'mic' else 'output'} device named like {fragment!r} (found: {names})")


class _LoopbackDevice(miniaudio.CaptureDevice):
    """``miniaudio.CaptureDevice`` opened as a loopback of a playback device (its constructor, with
    ``ma_device_type_loopback``; in loopback mode miniaudio reads the playback device's id from the
    capture side of the config)."""

    def __init__(self, device_id: Any = None) -> None:  # noqa: D107 — mirrors CaptureDevice.__init__
        from _miniaudio import ffi, lib

        miniaudio.AbstractDevice.__init__(self)
        self.format = miniaudio.SampleFormat.SIGNED16
        self.sample_width, self.nchannels, self.sample_rate = SAMPLE_BYTES, 1, SAMPLE_RATE
        self.buffersize_msec = BLOCK_MS
        self._ffi_handle = ffi.new_handle(self)
        self._devconfig = lib.ma_device_config_init(lib.ma_device_type_loopback)
        self._devconfig.sampleRate = SAMPLE_RATE
        self._devconfig.capture.channels = 1
        self._devconfig.capture.format = self.format.value
        self._devconfig.capture.pDeviceID = device_id or ffi.NULL
        self._devconfig.periodSizeInMilliseconds = BLOCK_MS
        self._devconfig.pUserData = self._ffi_handle
        self._devconfig.dataCallback = lib._internal_data_callback
        self._devconfig.stopCallback = lib._internal_stop_callback
        self._devconfig.periods = 0
        self.callback_generator = None
        self._context = self._make_context([miniaudio.Backend.WASAPI], miniaudio.ThreadPriority.HIGHEST, "facilitation-suite")
        result = lib.ma_device_init(self._context, ffi.addressof(self._devconfig), self._device)
        if result != lib.MA_SUCCESS:
            raise miniaudio.MiniaudioError("failed to init loopback device", result)
        self.backend = ffi.string(lib.ma_get_backend_name(self._device.pContext.backend)).decode()


class DeviceSource:
    """One live channel from an audio device; ``sink(speaker, pcm)`` is called on miniaudio's thread."""

    def __init__(self, speaker: str, kind: str, device_name: str, sink: PcmSink) -> None:
        self.speaker, self.kind, self.device_name, self.sink = speaker, kind, device_name, sink
        self._device: Optional[miniaudio.CaptureDevice] = None

    @property
    def label(self) -> str:
        return KIND_LABEL[self.kind]

    def start(self) -> None:
        try:
            dev_id = _device_id(self.kind, self.device_name)
            if self.kind == "mic":
                device = miniaudio.CaptureDevice(miniaudio.SampleFormat.SIGNED16, 1, SAMPLE_RATE, BLOCK_MS, dev_id,
                                                 backends=[miniaudio.Backend.WASAPI], app_name="facilitation-suite")
            else:
                device = _LoopbackDevice(dev_id)
            pump = self._pump()
            next(pump)
            device.start(pump)
        except (miniaudio.MiniaudioError, CaptureError) as exc:
            raise CaptureError(f"{self.label} not available ({exc})") from exc
        self._device = device
        logger.info("✅ copilot: %s capture started (%s)", self.label, self.device_name or "Windows default")

    def _pump(self):  # noqa: ANN202 — miniaudio's callback generator
        while True:
            data = yield
            self.sink(self.speaker, bytes(data))

    def stop(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None
            logger.info("ℹ️ copilot: %s capture stopped", self.label)


class FileSource:
    """One channel played in from a 16 kHz mono WAV at real-time pace, on its own thread."""

    def __init__(self, speaker: str, path: Path, sink: PcmSink) -> None:
        self.speaker, self.path, self.sink = speaker, path, sink
        self.label = f"File {path.name}"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        try:
            pcm = read_pcm(self.path)
        except (OSError, ValueError, EOFError) as exc:
            raise CaptureError(f"{self.label} not available ({exc})") from exc
        self._stop.clear()
        self._thread = threading.Thread(target=self._play, args=(pcm,), name=f"copilot-feed-{self.speaker}", daemon=True)
        self._thread.start()
        logger.info("✅ copilot: feeding %s as %s (%.0f s)", self.path.name, self.speaker, len(pcm) / SAMPLE_BYTES / SAMPLE_RATE)

    def _play(self, pcm: bytes) -> None:
        block = SAMPLE_RATE * SAMPLE_BYTES * BLOCK_MS // 1000
        began = time.monotonic()
        for n, at in enumerate(range(0, len(pcm), block)):
            wait = began + n * BLOCK_MS / 1000 - time.monotonic()
            if self._stop.wait(max(0.0, wait)):
                return
            self.sink(self.speaker, pcm[at:at + block])
        logger.info("ℹ️ copilot: %s played to the end", self.path.name)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
