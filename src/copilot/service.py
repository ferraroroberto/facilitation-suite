"""The live copilot: two channels recorded, chunked, transcribed through the hub, and discreet hints.

Switched on and off by hand (``copilot_toggle``: the presenter's switch, the Stream Deck) while a
session is live; it is off whenever a session goes live, closes or is replaced. While it is on:

- each channel (``me``: the facilitator's mic, ``them``: the call's sound) is written to
  ``live/copilot/<speaker>-<time>.wav`` as it arrives and cut into chunks (``vad.py``);
- each chunk is transcribed once by the hub (``transcribe_model``, the session's language) and
  appended to ``live/copilot/transcript.jsonl`` as ``{t0, t1, speaker, text, lag_ms}``; the
  transcript is never sent again as a whole;
- hints are asked on a new ``them`` line at most once per ``hint_interval_s``, and on every item
  change; each answer goes to ``live/copilot/hints.jsonl`` with its model and latency.

Its state rides on every live snapshot as ``copilot`` (the presenter's card and the overlay read
it). Hints belong to the item they were asked for and live ``hint_ttl_s``: an item change clears
them and an answer for an item no longer on stage is dropped, so a stale hint is never shown.
Failures stay apart — a channel that cannot open, the hub down, a transcript falling behind, a hint
that timed out — and none blocks the live session: capture, transcription and hint calls run on
their own threads, and every change to this state is made on the server's loop.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.config import COPILOT_CAPTURE, CopilotConfig
from src.copilot.audio import WavRecorder, wav_bytes
from src.copilot.capture import CaptureError, DeviceSource, FileSource
from src.copilot.hints import Answer, Line, ask_chain, clock_state, system_prompt, user_prompt
from src.copilot.hub_client import HubDown, HubFailure, complete, transcribe
from src.copilot.vad import Chunk, Chunker
from src.errors import DomainError
from src.jsonl import JsonlAppender
from src.live.hub import LiveHub, now_ms
from src.sessions.model import Session
from src.sessions.store import SessionError

logger = logging.getLogger(__name__)

TRANSCRIPT_FILE = "transcript.jsonl"
HINTS_FILE = "hints.jsonl"
SPEAKERS = ("me", "them")
# The presenter says the transcript is behind once the oldest chunk still waiting ended this long ago.
BEHIND_WARN_S = 15
# Whisper takes seconds for a 45-s chunk; a dictation queued ahead of it on the shared server adds more.
TRANSCRIBE_TIMEOUT_S = 90
# Whisper's own words for "nothing here" ("[BLANK_AUDIO]", "(music)") are not speech.
_NOT_SPEECH = re.compile(r"^[\[(][^\])]*[\])]$|^[\s.…,!?-]*$")
PROBLEM_TEXT = {
    "no_hub": "No hub configured (copilot.hub_url)",
    "hub_down": "Hub down",
    "transcribe_error": "Transcription failed",
    "hint_timeout": "Hint timed out",
    "hint_error": "Hint failed",
}


class CopilotError(DomainError):
    """A copilot request that cannot run now."""


@dataclass
class _HintRequest:
    seq: int
    item_id: Optional[str]
    reason: str
    messages: list[dict[str, str]]
    trigger_at: float


@dataclass
class _Run:
    """One stretch of Copilot on: its queues, devices and files. Its workers end when it stops."""

    session_id: str
    language: str
    local_only: bool
    pcm: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1200))
    chunks: queue.Queue = field(default_factory=queue.Queue)
    hints: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: dict[int, float] = field(default_factory=dict)  # chunk seq → when its audio ended
    sources: list[Any] = field(default_factory=list)
    chunkers: dict[str, Chunker] = field(default_factory=dict)
    recorders: dict[str, WavRecorder] = field(default_factory=dict)
    seq: int = 0
    dropped: int = 0


class CopilotService:
    def __init__(self, live: LiveHub, config: Callable[[], CopilotConfig]) -> None:
        self.live = live
        self.config = config
        self.on = False
        self.local_only = False
        self.started_at: Optional[int] = None
        self.channels: dict[str, dict[str, str]] = {}
        self.problems: dict[str, str] = {}
        self.hints: list[dict[str, str]] = []
        self.hints_at: Optional[int] = None
        self.hints_item_id: Optional[str] = None
        self.hints_model = ""
        self.lines: deque[Line] = deque(maxlen=4000)
        self.last_lag_ms: Optional[int] = None
        self.last_hint_ms: Optional[int] = None
        self.stats = {"chunks": 0, "lines": 0, "hints": 0}
        # On/off: the overlay window follows (the server wires it; src/ never imports app/).
        self.listeners: list[Callable[[bool], None]] = []
        self.overlay_running: Callable[[], bool] = lambda: False
        self.on_sample: Callable[[], None] = lambda: None  # the server starts the overlay for a sample
        self.sample_at: Optional[int] = None
        self._run: Optional[_Run] = None
        self._hint_seq = 0
        self._shown_seq = 0
        self._last_hint_request = float("-inf")
        self._transcript_log = JsonlAppender(TRANSCRIPT_FILE, live.write_failed, live.write_ok)
        self._hints_log = JsonlAppender(HINTS_FILE, live.write_failed, live.write_ok)
        live.extra_state.append(lambda: {"copilot": self.snapshot()})
        live.close_listeners.append(self.stop)
        live.session_listeners.append(self._session_changed)
        live.reset_listeners.append(self._reset)
        live.item_listeners.append(self._item_changed)

    # ------------------------------------------------------------------ helpers

    def _on_loop(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run ``fn`` on the server's loop (directly when there is none: unit tests)."""
        if self.live.loop is None:
            fn(*args)
        else:
            self.live.loop.call_soon_threadsafe(fn, *args)

    def _dir(self) -> Optional[Path]:
        return self.live.folder / "live" / "copilot" if self.live.folder is not None else None

    def _load_session(self) -> Optional[Session]:
        if self.live.session_id is None:
            return None
        try:
            return self.live.store.load(self.live.session_id)
        except SessionError as exc:
            logger.warning("⚠️ copilot: session %s unreadable (%s) — local only stays as it was", self.live.session_id, exc)
            return None

    # --------------------------------------------------------------- lifecycle

    def toggle(self) -> None:
        if self.on:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.on:
            return
        if self.live.session_id is None:
            raise CopilotError(409, "not_live", "No session is live — Copilot runs during a live session")
        cfg = self.config()
        if cfg.capture not in COPILOT_CAPTURE:
            logger.warning("⚠️ copilot: capture %r is not one of %s — recording nothing", cfg.capture, ", ".join(COPILOT_CAPTURE))
        session = self._load_session()
        if session is not None:
            self.local_only = session.copilot_local_only
        run = _Run(self.live.session_id, str(self.live.run.get("language") or "en"), self.local_only)
        stamp = datetime.now().strftime("%H%M%S")
        folder = self._dir()
        for speaker in SPEAKERS:
            run.chunkers[speaker] = Chunker(speaker, silence_dbfs=cfg.silence_dbfs, min_s=cfg.min_chunk_s,
                                            max_s=cfg.max_chunk_s, silence_ms=cfg.silence_ms)
            if folder is not None:
                run.recorders[speaker] = WavRecorder(folder / f"{speaker}-{stamp}.wav", self._write_failed, self._write_ok)
        self._run, self.on, self.started_at = run, True, now_ms()
        self.problems = {"no_hub": PROBLEM_TEXT["no_hub"]} if not cfg.hub_url.strip() else {}
        self.hints, self.hints_at, self.hints_item_id, self._last_hint_request = [], None, None, float("-inf")
        self.channels = self._planned_channels(cfg)
        for target, name in ((self._chunk_worker, "chunks"), (self._transcribe_worker, "transcribe"),
                             (self._hint_worker, "hints"), (self._open_sources, "sources")):
            threading.Thread(target=target, args=(run,), name=f"copilot-{name}", daemon=True).start()
        logger.info("✅ copilot on (capture %s, %s)", cfg.capture, "local only" if self.local_only else ", ".join(self._models(run)))
        self.live.event("copilot_on", capture=cfg.capture, local_only=self.local_only)
        self._tell(True)
        self.live.push_state()

    def stop(self) -> None:
        run = self._run
        if not self.on or run is None:
            return
        self._run, self.on = None, False
        self.hints, self.hints_at, self.channels = [], None, {}
        run.stopped.set()
        run.hints.put(None)
        threading.Thread(target=self._close_sources, args=(run,), name="copilot-close", daemon=True).start()
        logger.info("ℹ️ copilot off (%d lines, %d hints)", self.stats["lines"], self.stats["hints"])
        self.live.event("copilot_off")
        self._tell(False)
        self.live.push_state()

    def _tell(self, on: bool) -> None:
        for fn in self.listeners:
            try:
                fn(on)
            except Exception:  # noqa: BLE001 — the overlay failing must never stop the copilot
                logger.exception("❌ copilot: a listener failed on %s", "on" if on else "off")

    def _planned_channels(self, cfg: CopilotConfig) -> dict[str, dict[str, str]]:
        if cfg.capture not in ("devices", "files"):
            return {}
        labels = {"me": "Mic", "them": "Call audio"} if cfg.capture == "devices" else {"me": "me.wav", "them": "them.wav"}
        return {s: {"label": labels[s], "state": "starting", "detail": ""} for s in SPEAKERS}

    def _session_changed(self, sid: Optional[str]) -> None:
        self.stop()
        self.lines.clear()
        self.stats = {"chunks": 0, "lines": 0, "hints": 0}
        self.last_lag_ms = self.last_hint_ms = None
        self._transcript_log.forget()
        self._hints_log.forget()
        session = self._load_session() if sid else None
        self.local_only = bool(session and session.copilot_local_only)

    def _reset(self) -> None:
        """The presenter started the session over: the transcript so far went with the old ``live/``."""
        self.lines.clear()
        self.hints, self.hints_at = [], None
        self.stats = {"chunks": 0, "lines": 0, "hints": 0}
        self._transcript_log.forget()
        self._hints_log.forget()

    def sample(self) -> None:
        """Two sample hints on the overlay, Copilot on or off (Settings: where does it sit, how does it look).
        The time rides on the snapshot, so an overlay that starts for it still sees it when it connects."""
        self.sample_at = now_ms()
        self.on_sample()
        self.live.push_state()

    def set_local_only(self, value: bool) -> None:
        """Save the session's local-only switch in ``session.yaml`` (the file first: a failed save changes nothing)."""
        if self.live.session_id is None:
            raise CopilotError(409, "not_live", "No session is live")

        def change(session: Session) -> None:
            session.copilot_local_only = value

        try:
            self.live.store.edit(self.live.session_id, change)
        except (SessionError, OSError) as exc:
            logger.error("❌ copilot: local only could not be saved (%s)", exc)
            raise CopilotError(500, "local_only_not_saved", "Local only could not be saved to session.yaml") from exc
        self.local_only = value
        if self._run is not None:
            self._run.local_only = value
        logger.info("ℹ️ copilot: hints from %s", "the local model only" if value else "the configured models")
        self.live.push_state()

    # ----------------------------------------------------------------- capture

    def _sources(self, run: _Run, cfg: CopilotConfig) -> list[Any]:
        def sink(speaker: str, pcm: bytes) -> None:
            try:
                run.pcm.put_nowait((speaker, pcm))
            except queue.Full:
                run.dropped += 1

        if cfg.capture == "devices":
            return [DeviceSource("me", "mic", cfg.mic_device, sink), DeviceSource("them", "loopback", cfg.call_device, sink)]
        if cfg.capture == "files":
            return [FileSource(s, Path(cfg.feed_dir) / f"{s}.wav", sink) for s in SPEAKERS]
        return []

    def _open_sources(self, run: _Run) -> None:
        for source in self._sources(run, self.config()):
            if run.stopped.is_set():
                return
            try:
                source.start()
            except CaptureError as exc:
                logger.error("❌ copilot: %s", exc)
                self._on_loop(self._channel, run, source.speaker, "error", str(exc))
                continue
            run.sources.append(source)
            self._on_loop(self._channel, run, source.speaker, "recording", "")
        if run.stopped.is_set():
            self._close_sources(run)

    def _channel(self, run: _Run, speaker: str, state: str, detail: str) -> None:
        if run is self._run and speaker in self.channels:
            self.channels[speaker] = {**self.channels[speaker], "state": state, "detail": detail}
            self.live.push_state()

    def _close_sources(self, run: _Run) -> None:
        while run.sources:
            run.sources.pop().stop()

    def _write_failed(self, name: str, exc: OSError) -> None:
        self._on_loop(self._report_write, name, exc)

    def _report_write(self, name: str, exc: Optional[OSError]) -> None:
        if exc is not None:
            self.live.write_failed(name, exc)
        elif not self.live.write_ok(name):
            return
        self.live.push_state()

    def _write_ok(self, name: str) -> None:
        self._on_loop(self._report_write, name, None)

    def _chunk_worker(self, run: _Run) -> None:
        while True:
            try:
                speaker, pcm = run.pcm.get(timeout=0.5)
            except queue.Empty:
                if run.stopped.is_set():
                    break
                continue
            if speaker in run.recorders:
                run.recorders[speaker].write(pcm)
            for chunk in run.chunkers[speaker].feed(pcm):
                self._queue_chunk(run, chunk)
        for speaker in SPEAKERS:
            for chunk in run.chunkers[speaker].flush():
                self._queue_chunk(run, chunk)
            if speaker in run.recorders:
                run.recorders[speaker].close()
        if run.dropped:
            logger.warning("⚠️ copilot: %d audio block(s) dropped — the chunker fell behind the devices", run.dropped)
        run.chunks.put(None)

    def _queue_chunk(self, run: _Run, chunk: Chunk) -> None:
        with run.lock:
            run.seq += 1
            run.pending[run.seq] = chunk.ended_at
            seq = run.seq
        run.chunks.put((seq, chunk))

    # ----------------------------------------------------------- transcription

    def _transcribe_worker(self, run: _Run) -> None:
        while True:
            item = run.chunks.get()
            if item is None:
                return
            seq, chunk = item
            cfg = self.config()
            text, failure = "", None
            if cfg.hub_url.strip():
                try:
                    out = transcribe(cfg.hub_url, wav_bytes(chunk.pcm), model=cfg.transcribe_model,
                                     language=run.language, timeout=TRANSCRIBE_TIMEOUT_S)
                    text = " ".join(str(out.get("text") or "").split())
                except HubFailure as exc:
                    failure = exc
            with run.lock:
                run.pending.pop(seq, None)
            self._on_loop(self._transcribed, run, chunk, text, failure, time.time())

    def _transcribed(self, run: _Run, chunk: Chunk, text: str, failure: Optional[HubFailure], done_at: float) -> None:
        self.stats["chunks"] += 1
        if failure is not None:
            code = "hub_down" if isinstance(failure, HubDown) else "transcribe_error"
            self._clear_problems("transcribe_error", "hub_down")
            if code not in self.problems:
                logger.error("❌ copilot: %s chunk %.0f–%.0f s not transcribed (%s)", chunk.speaker, chunk.t0, chunk.t1, failure)
            self.problems[code] = f"{PROBLEM_TEXT[code]} — {failure}"
            self.live.push_state()
            return
        self._clear_problems("transcribe_error", "hub_down")
        if not text or _NOT_SPEECH.match(text) or run.session_id != self.live.session_id:
            self.live.push_state()
            return
        lag_ms = int((done_at - chunk.ended_at) * 1000)
        self.last_lag_ms = lag_ms
        self._add_line(chunk.speaker, text, chunk.ended_at, {"t0": chunk.t0, "t1": chunk.t1, "lag_ms": lag_ms})
        if chunk.speaker == "them" and run is self._run:
            self._maybe_hint("them")
        self.live.push_state()

    def _add_line(self, speaker: str, text: str, ended_at: float, extra: dict[str, Any]) -> None:
        self.lines.append(Line(speaker, text, ended_at))
        self.stats["lines"] += 1
        folder = self._dir()
        if folder is not None:
            rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), **extra, "speaker": speaker, "text": text}
            self._transcript_log.append(folder / TRANSCRIPT_FILE, [rec])

    def inject(self, lines: list[tuple[str, str]]) -> int:
        """Lines added as if transcribed (a test, a rehearsal): they always ask for hints at once."""
        if not self.on:
            raise CopilotError(409, "copilot_off", "Switch Copilot on first")
        now = time.time()
        for speaker, text in lines:
            self._add_line(speaker, text, now, {"injected": True})
        self._request_hint("inject")
        self.live.push_state()
        return len(lines)

    # ------------------------------------------------------------------- hints

    def _item_changed(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        if not self.on:
            return
        self.hints, self.hints_at = [], None  # they were for the item that just left
        self._request_hint("item")

    def _maybe_hint(self, reason: str) -> None:
        if time.monotonic() - self._last_hint_request >= self.config().hint_interval_s:
            self._request_hint(reason)

    def _request_hint(self, reason: str) -> None:
        run = self._run
        if run is None:
            return
        cfg = self.config()
        self._last_hint_request = time.monotonic()
        self._hint_seq += 1
        cur = self.live.current()
        nxt = next((it for it in self.live.items[(cur["index"] + 1) if cur else 0:] if not it.get("hidden")), None)
        now = time.time()
        tail = [ln for ln in self.lines if now - ln.ended_at <= cfg.context_minutes * 60]
        clock = clock_state(self.live.run, cur, self.live.clock_started_at, self.live.section_entered,
                            self.live.duration_minutes, now_ms())
        messages = [{"role": "system", "content": system_prompt(run.language)},
                    {"role": "user", "content": user_prompt(self.live.session_title, cur, nxt, clock, tail, now)}]
        request = _HintRequest(self._hint_seq, cur["id"] if cur else None, reason, messages, now)
        try:
            run.hints.get_nowait()  # an older request still waiting is superseded by this one
        except queue.Empty:
            pass
        run.hints.put_nowait(request)

    def _models(self, run: _Run) -> list[str]:
        cfg = self.config()
        chain = [cfg.local_model] if run.local_only else [*cfg.hint_models, cfg.local_model]
        return list(dict.fromkeys(str(m).strip() for m in chain if str(m).strip()))

    def _hint_worker(self, run: _Run) -> None:
        while True:
            request = run.hints.get()
            if request is None:
                return
            cfg = self.config()
            if not cfg.hub_url.strip():
                answer = Answer(failure="error", detail=PROBLEM_TEXT["no_hub"])
            else:
                answer = ask_chain(lambda m, msgs, t, url=cfg.hub_url: complete(url, m, msgs, timeout=t),
                                   self._models(run), request.messages, cfg.hint_timeout_s)
            self._on_loop(self._hinted, run, request, answer, time.time())

    def _hinted(self, run: _Run, request: _HintRequest, answer: Answer, done_at: float) -> None:
        lag_ms = int((done_at - request.trigger_at) * 1000)
        cur = self.live.current()
        stale = run is not self._run or (cur["id"] if cur else None) != request.item_id or request.seq < self._shown_seq
        folder = self._dir()
        if folder is not None and run.session_id == self.live.session_id:
            rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "reason": request.reason,
                   "item_id": request.item_id, "model": answer.model, "lag_ms": lag_ms,
                   "hints": [h.as_dict() for h in answer.hints], **({"failure": answer.failure, "detail": answer.detail} if answer.failure else {}),
                   **({"dropped": "stale"} if stale and not answer.failure else {})}
            self._hints_log.append(folder / HINTS_FILE, [rec])
        if run is not self._run:
            return
        self._clear_problems("hint_timeout", "hint_error", "hub_down")  # the latest answer says how the hub is
        if answer.failure:
            code = {"hub_down": "hub_down", "timeout": "hint_timeout"}.get(answer.failure, "hint_error")
            logger.warning("⚠️ copilot: no hint (%s: %s)", code, answer.detail)
            self.problems[code] = f"{PROBLEM_TEXT[code]} — {answer.detail}"
            self.live.push_state()
            return
        self.last_hint_ms = lag_ms
        if not stale and answer.hints:
            self._shown_seq = request.seq
            self.hints = [h.as_dict() for h in answer.hints]
            self.hints_at, self.hints_item_id, self.hints_model = now_ms(), request.item_id, answer.model
            self.stats["hints"] += len(answer.hints)
            if self.live.loop is not None:
                self.live.loop.call_later(self.config().hint_ttl_s, self._expire, self.hints_at)
        self.live.push_state()

    def _clear_problems(self, *codes: str) -> None:
        for code in codes:
            self.problems.pop(code, None)

    def _expire(self, at: int) -> None:
        if self.hints_at == at:
            self.hints, self.hints_at = [], None
            self.live.push_state()

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        cfg = self.config()
        run = self._run
        behind = 0.0
        if run is not None:
            with run.lock:
                oldest = min(run.pending.values(), default=None)
            behind = max(0.0, time.time() - oldest) if oldest is not None else 0.0
        fresh = self.hints_at is not None and now_ms() - self.hints_at < cfg.hint_ttl_s * 1000
        return {
            "on": self.on,
            "started_at": self.started_at if self.on else None,
            "local_only": self.local_only,
            "capture": cfg.capture,
            "channels": [{"speaker": s, **c} for s, c in self.channels.items()],
            "behind_s": round(behind),
            "behind_warn_s": BEHIND_WARN_S,
            "problems": [{"code": k, "message": v} for k, v in self.problems.items()],
            "hints": self.hints if fresh else [],
            "hints_at": self.hints_at if fresh else None,
            "hints_item_id": self.hints_item_id if fresh else None,
            "hints_model": self.hints_model if fresh else "",
            "ttl_s": cfg.hint_ttl_s,
            "last_lag_ms": self.last_lag_ms,
            "last_hint_ms": self.last_hint_ms,
            "stats": dict(self.stats),
            "sample_at": self.sample_at,
            "overlay": {**asdict(cfg.overlay), "running": self.overlay_running()},
        }
