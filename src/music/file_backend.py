"""Audio files played on this PC's default output device (miniaudio, in process).

miniaudio (a cp314 wheel, no system install) decodes mp3, wav, ogg (Vorbis)
and flac to 44.1 kHz float stereo and plays to the default device. The
device pulls audio through one **mixer** generator on miniaudio's own audio
thread; every track being played is a ``Voice`` with its own decoder and gain
envelope, so fades are per-sample ramps (no steps, no clicks) and a new track
crossfades over the one it replaces.

Threads: the public methods only queue a command. A worker thread opens
decoders and the device (``ma_device_init`` can take a few hundred ms) and
closes the device again once nothing has played for a few seconds. The audio
thread only mixes; what it notices (sound started, a file ended) goes back to
the worker through the same queue, which tells the service.
"""

from __future__ import annotations

import array
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator, Iterator
from typing import Any, Optional

from src.music.backend import EventSink, Track

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
BUFFER_MS = 100
DECLICK_S = 0.05  # the shortest fade: a cut would click
VOLUME_RAMP_S = 0.3  # a volume change glides instead of jumping
IDLE_CLOSE_S = 5.0  # the device is released after this long with nothing to play

Source = Iterator[array.array]  # a primed miniaudio stream: send(frames) → float samples


def level(volume: float) -> float:
    """0–100 → amplitude. Squared, so the slider's middle sounds like the middle."""
    v = max(0.0, min(100.0, float(volume))) / 100.0
    return v * v


def open_source(path: str) -> Source:
    import miniaudio

    return miniaudio.stream_file(path, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=CHANNELS,
                                 sample_rate=SAMPLE_RATE, frames_to_read=SAMPLE_RATE * BUFFER_MS // 1000)


def open_device() -> Any:
    import miniaudio

    return miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.FLOAT32, nchannels=CHANNELS,
                                    sample_rate=SAMPLE_RATE, buffersize_msec=BUFFER_MS, app_name="facilitation-suite")


class Voice:
    """One track being played: its decoder and its gain envelope.

    ``ramp`` sets where the gain goes and how fast; ``then`` says what happens
    when a ramp down to silence arrives — ``"pause"`` holds the position,
    ``"stop"`` ends the voice. Touched by the worker and the audio thread,
    always under the backend's lock.
    """

    def __init__(self, play_id: int, track: Track, opener: Callable[[str], Source]) -> None:
        self.play_id, self.track, self._open = play_id, track, opener
        self.src = opener(track.ref)
        self.gain = 0.0
        self.target = 0.0
        self.step = 0.0
        self.then: Optional[str] = None
        self.paused = False
        self.done = False
        self.ended = False  # reached the end of the file (not looping)
        self.error = ""
        self.frames = 0  # frames played so far (the position)

    def ramp(self, target: float, seconds: float, then: Optional[str] = None) -> None:
        self.target, self.then = target, then
        frames = seconds * SAMPLE_RATE
        if frames < 1:
            self.gain, self.step = target, 0.0
            self._arrived()
        else:
            self.step = (target - self.gain) / frames

    def _arrived(self) -> None:
        if self.then and self.target <= 0.0:
            if self.then == "pause":
                self.paused = True
            else:
                self.done = True
        self.then = None

    def _pull(self, frames: int) -> array.array:
        try:
            return self.src.send(frames)
        except StopIteration:
            return array.array("f")

    def _read(self, frames: int) -> array.array:
        data = self._pull(frames)
        if self.track.loop and len(data) < frames * CHANNELS:  # the end of the file: straight on from the top
            self.src = self._open(self.track.ref)
            data = array.array("f", data)
            data.extend(self._pull(frames - len(data) // CHANNELS))
        return data

    def mix(self, out: array.array, frames: int) -> None:
        """Add this voice's next ``frames`` frames, times its gain envelope, into ``out``."""
        if self.paused or self.done:
            return
        data = self._read(frames)
        n = min(len(data) // CHANNELS, frames)
        if n == 0:
            self.done = self.ended = True
            return
        g, target, step = self.gain, self.target, self.step
        if g == target:
            for j in range(n * CHANNELS):
                out[j] += data[j] * g
        else:
            for i in range(n):
                if g != target:
                    g += step
                    if (step >= 0 and g >= target) or (step < 0 and g <= target):
                        g = target
                j = i * CHANNELS
                out[j] += data[j] * g
                out[j + 1] += data[j + 1] * g
        self.gain = g
        self.frames += n
        if g == target and self.then:
            self._arrived()


class FileBackend:
    kind = "file"

    def __init__(self, on_event: EventSink, *, device_factory: Callable[[], Any] = open_device,
                 source_factory: Callable[[str], Source] = open_source) -> None:
        self.on_event = on_event
        self._device_factory, self._source_factory = device_factory, source_factory
        self._lock = threading.Lock()
        self._voices: list[Voice] = []
        self._current: Optional[Voice] = None
        self._queue: queue.Queue = queue.Queue()
        self._device: Any = None
        self._idle_since: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self.device_name = ""

    # ---------------------------------------------------------------- interface

    def play(self, play_id: int, track: Track, volume: int, fade_in_s: float) -> None:
        self._submit(lambda: self._play(play_id, track, volume, fade_in_s))

    def pause(self, fade_s: float) -> None:
        self._submit(lambda: self._with_current(lambda v: v.ramp(0.0, max(fade_s, DECLICK_S), "pause")))

    def resume(self, volume: int, fade_s: float) -> None:
        self._submit(lambda: self._resume(volume, fade_s))

    def stop(self, fade_s: float) -> None:
        self._submit(lambda: self._stop(fade_s))

    def set_volume(self, volume: int) -> None:
        def glide(v: Voice) -> None:
            if v.then is None and not v.paused:  # a fade-out in progress keeps going
                v.ramp(level(volume), VOLUME_RAMP_S)
        self._submit(lambda: self._with_current(glide))

    def fade(self, to: int, seconds: float) -> None:
        self._submit(lambda: self._with_current(lambda v: v.ramp(level(to), max(seconds, DECLICK_S))))

    def snapshot(self) -> dict[str, Any]:
        v = self._current
        return {"device": self.device_name, "position_s": round(v.frames / SAMPLE_RATE, 1) if v else None}

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(("quit", None))
            self._thread.join(timeout=3)
        self._close_device()

    # ------------------------------------------------------------------ worker

    def _submit(self, fn: Callable[[], None]) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="music-file", daemon=True)
            self._thread.start()
        self._queue.put(("do", fn))

    def _run(self) -> None:
        while True:
            try:
                kind, arg = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._maybe_close()
                continue
            if kind == "quit":
                return
            if kind == "event":
                self._emit(*arg)
                continue
            try:
                arg()
            except Exception as exc:  # noqa: BLE001 — a command that fails is an error state, never a dead worker
                logger.exception("❌ music (file): command failed")
                self._emit("error", self._current.play_id if self._current else 0, f"Playback failed ({exc})")
            self._maybe_close()

    def _emit(self, event: str, play_id: int, detail: str = "") -> None:
        try:
            self.on_event(event, play_id, detail)
        except RuntimeError:  # the server's event loop is already closed
            pass

    def _with_current(self, fn: Callable[[Voice], None]) -> None:
        with self._lock:
            if self._current is not None and not self._current.done:
                fn(self._current)

    def _play(self, play_id: int, track: Track, volume: int, fade_in_s: float) -> None:
        try:
            voice = Voice(play_id, track, self._source_factory)
        except Exception as exc:  # noqa: BLE001 — miniaudio.DecodeError, a missing file, an unreadable one
            logger.warning("⚠️ music (file): cannot open %s (%s)", track.ref, exc)
            self._emit("error", play_id, f"Cannot play {track.label} ({exc})")
            return
        voice.ramp(level(volume), fade_in_s)
        with self._lock:
            for old in self._voices:  # the latest command wins: the old track crossfades out
                if old.paused:
                    old.done = True
                else:
                    old.ramp(0.0, max(min(fade_in_s, 2.0), DECLICK_S), "stop")
            self._voices = [v for v in self._voices if not v.done] + [voice]
            self._current = voice
        self._ensure_device(play_id)
        logger.info("ℹ️ music (file): play %s (volume %d, fade in %.1f s%s)", track.label, volume, fade_in_s,
                    ", loop" if track.loop else "")

    def _resume(self, volume: int, fade_s: float) -> None:
        with self._lock:
            v = self._current
            if v is None or v.done:
                return
            v.paused = False
            v.ramp(level(volume), fade_s)
        self._ensure_device(v.play_id)

    def _stop(self, fade_s: float) -> None:
        with self._lock:
            for v in self._voices:
                if v.paused:
                    v.done = True
                else:
                    v.ramp(0.0, max(fade_s, DECLICK_S), "stop")
            self._voices = [v for v in self._voices if not v.done]
            self._current = None

    def _ensure_device(self, play_id: int) -> None:
        self._idle_since = None
        if self._device is not None:
            return
        try:
            device = self._device_factory()
            gen = self._mixer()
            next(gen)
            device.start(gen)
        except Exception as exc:  # noqa: BLE001 — no output device, the driver refused
            logger.error("❌ music (file): no audio output (%s)", exc)
            with self._lock:
                self._voices, self._current = [], None
            self._emit("error", play_id, f"No audio output device on this PC — are the speakers or headset connected? ({exc})")
            return
        self._device = device
        self.device_name = str(getattr(device, "backend", "") or "audio device")
        logger.info("ℹ️ music (file): output open (%s, %d Hz)", self.device_name, SAMPLE_RATE)

    def _maybe_close(self) -> None:
        if self._device is None:
            return
        with self._lock:
            busy = bool(self._voices)
        if busy:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = time.monotonic()
        elif time.monotonic() - self._idle_since >= IDLE_CLOSE_S:
            self._close_device()

    def _close_device(self) -> None:
        device, self._device = self._device, None
        self._idle_since = None
        if device is not None:
            try:
                device.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️ music (file): closing the output failed (%s)", exc)
            logger.info("ℹ️ music (file): output closed")

    # --------------------------------------------------------------- audio thread

    def mix(self, frames: int) -> array.array:
        """The next ``frames`` frames of every voice, mixed (the audio thread's one job)."""
        out = array.array("f", bytes(frames * CHANNELS * 4))
        events: list[tuple[str, int, str]] = []
        with self._lock:
            for v in self._voices:
                started = v.frames == 0
                try:
                    v.mix(out, frames)
                except Exception as exc:  # noqa: BLE001 — a decoder failing mid-file ends that voice only
                    v.done, v.error = True, str(exc)
                if started and v.frames:
                    events.append(("playing", v.play_id, v.track.label))
                if v.done:
                    if v.error:
                        events.append(("error", v.play_id, f"{v.track.label} stopped playing ({v.error})"))
                    elif v.ended:
                        events.append(("ended", v.play_id, v.track.label))
            if any(v.done for v in self._voices):
                self._voices = [v for v in self._voices if not v.done]
                if self._current is not None and self._current.done:
                    self._current = None
        for e in events:
            self._queue.put(("event", e))
        return out

    def _mixer(self) -> Generator[array.array, int]:
        frames = yield array.array("f")
        while True:
            try:
                out = self.mix(frames)
            except Exception:  # noqa: BLE001 — never let the audio thread die: silence instead
                logger.exception("❌ music (file): mixing failed")
                out = array.array("f", bytes(frames * CHANNELS * 4))
            frames = yield out
