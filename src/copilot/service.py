"""The live copilot: two channels recorded, chunked, transcribed through the hub, and discreet hints.

Switched on and off by hand (``copilot_toggle``: the presenter's switch, the Stream Deck) while a
session is live; it is off whenever a session goes live, closes or is replaced. While it is on:

- each channel (``me``: the facilitator's mic, ``them``: the call's sound) is written to
  ``live/copilot/<speaker>-<time>.wav`` as it arrives and cut into chunks (``vad.py``);
- each chunk is transcribed once by the hub (``transcribe_model``, the session's language) and
  appended to ``live/copilot/transcript.jsonl`` as ``{t0, t1, speaker, text, lag_ms}``; the
  transcript is never sent again as a whole;
- hints are asked on a new ``them`` line at most once per ``hint_interval_s``, and on every item
  change; each answer goes to ``live/copilot/hints.jsonl`` with its model and latency;
- every ``summary_interval_s`` the lines heard since the last refresh are folded into a rolling
  summary (``summary.py``, ``live/copilot/summary.json``) that rides in every hint prompt;
- a section (or the session) ``drift_hint_minutes`` behind the plan gets one ``drift`` hint,
  computed from the clocks with no model call;
- hint and summary calls are capped per sliding hour; a cap reached is its own line on the card.

The session's mode shapes the prompts: ``workshop`` (``them`` is everyone else on the call) or ``interview``
(``them`` is the interviewee, by name, and hints lean to follow-up questions); ``transcript.jsonl`` keeps the
channel names either way.

This module owns the switch, the session's settings, capture and transcription, and composes the hint and
drift pipeline (``hint_pipeline.py``) and the summary pipeline (``summary_pipeline.py``); what they share is
``context.py``.

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
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.config import COPILOT_CAPTURE, CopilotConfig
from src.copilot.audio import WavRecorder, wav_bytes
from src.copilot.capture import CaptureError, DeviceSource, FileSource
from src.copilot.channel_state import ChannelState, advance, is_failed, is_recording, is_starting
from src.copilot.context import HINTS_FILE, PROBLEM_TEXT, WARN_PROBLEMS, CopilotContext, Run, Status
from src.copilot.hint_pipeline import HintPipeline
from src.copilot.hints import NOT_SPEECH, Line
from src.copilot.hub_client import HubDown, HubFailure, transcribe
from src.copilot.retention import purge_recordings
from src.copilot.summary_pipeline import SummaryPipeline
from src.copilot.vad import Chunk, Chunker, frame_dbfs
from src.errors import DomainError
from src.jsonl import JsonlAppender
from src.live.hub import LiveHub, now_ms
from src.sessions.model import COPILOT_CONSENT, COPILOT_MODES, INTERVIEWEE_CHARS, Session
from src.sessions.store import SessionError

logger = logging.getLogger(__name__)

TRANSCRIPT_FILE = "transcript.jsonl"
SPEAKERS = ("me", "them")
# The presenter says the transcript is behind once the oldest chunk still waiting ended this long ago.
BEHIND_WARN_S = 15
# Whisper takes seconds for a 45-s chunk; a dictation queued ahead of it on the shared server adds more.
TRANSCRIBE_TIMEOUT_S = 90
# How often the clocks are checked for drift and a reached cap for room again, while Copilot is on.
TICK_S = 15


def _channel_view(speaker: str, channel: dict[str, str], level: Optional[tuple[float, float]], now: float) -> dict[str, Any]:
    """One channel on the snapshot: its state, the flags the presenter reads instead of spelling it, and its level."""
    state = ChannelState(channel["state"])
    return {"speaker": speaker, **channel,
            "starting": is_starting(state), "recording": is_recording(state), "failed": is_failed(state),
            "level": {"db": round(level[0], 1), "age_s": round(now - level[1], 1)} if level is not None else None}


class CopilotError(DomainError):
    """A copilot request that cannot run now."""


class CopilotService:
    def __init__(self, live: LiveHub, config: Callable[[], CopilotConfig]) -> None:
        self.live = live
        self.config = config
        self.on = False
        self.local_only = False
        self.mode = "workshop"
        self.interviewee = ""
        self.started_at: Optional[int] = None
        self.channels: dict[str, dict[str, str]] = {}
        self.lines: deque[Line] = deque(maxlen=4000)
        self.last_lag_ms: Optional[int] = None
        # When each channel last had a chunk transcribed, for the presenter's status.
        self.last_chunk_at: dict[str, int] = {}
        # On/off: the overlay window follows (the server wires it; src/ never imports app/).
        self.listeners: list[Callable[[bool], None]] = []
        self.overlay_running: Callable[[], bool] = lambda: False
        self.on_sample: Callable[[], None] = lambda: None  # the server starts the overlay for a sample
        self.sample_at: Optional[int] = None
        self._run: Optional[Run] = None
        self._transcript_log = JsonlAppender(TRANSCRIPT_FILE, live.write_failed, live.write_ok)
        self.status = Status(live.push_state)
        self._ctx = CopilotContext(live, config, self.status, run=lambda: self._run,
                                   persona=lambda: (self.mode, self.interviewee),
                                   hints_log=JsonlAppender(HINTS_FILE, live.write_failed, live.write_ok))
        self.summariser = SummaryPipeline(self._ctx)
        self.hinting = HintPipeline(self._ctx, self.lines, lambda: self.summariser.text)
        live.extra_state.append(lambda: {"copilot": self.snapshot()})
        live.close_listeners.append(self.stop)
        live.session_listeners.append(self._session_changed)
        live.reset_listeners.append(self._reset)
        live.item_listeners.append(self._item_changed)

    # ------------------------------------------------------------------ helpers

    def _load_session(self) -> Optional[Session]:
        if self.live.session_id is None:
            return None
        try:
            return self.live.store.load(self.live.session_id)
        except SessionError as exc:
            logger.warning("⚠️ copilot: session %s unreadable (%s) — its switches stay as they were", self.live.session_id, exc)
            return None

    # --------------------------------------------------------------- lifecycle

    def consented(self) -> bool:
        """The facilitator confirmed, for the live session, that participants were informed and consented."""
        return bool(self.live.checklist.get(COPILOT_CONSENT))

    def confirm_consent(self) -> None:
        """Record the confirmation in ``session.yaml`` (the file first: a failed save changes nothing) and in
        ``live/events.jsonl``. Asked once per session: a session already confirmed is left as it is."""
        sid = self.live.session_id
        if sid is None:
            raise CopilotError(409, "not_live", "No session is live")
        if self.consented():
            return

        def change(session: Session) -> None:
            session.checklist[COPILOT_CONSENT] = True

        self.live.edit_session(change, "consent_not_saved", "The confirmation could not be saved to session.yaml",
                               CopilotError)
        self.live.checklist[COPILOT_CONSENT] = True
        self.live.event("copilot_consent")
        logger.info("ℹ️ copilot: consent confirmed for session %s", sid)
        self.live.push_state()

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
        if not self.consented():
            raise CopilotError(409, "consent_required",
                               "Confirm that the participants were informed and consented before switching Copilot on")
        cfg = self.config()
        if cfg.capture not in COPILOT_CAPTURE:
            logger.warning("⚠️ copilot: capture %r is not one of %s — recording nothing", cfg.capture, ", ".join(COPILOT_CAPTURE))
        session = self._load_session()
        if session is not None:
            self._from_session(session)
        run = Run(self.live.session_id, str(self.live.run.get("language") or "en"), self.local_only)
        stamp = datetime.now().strftime("%H%M%S")
        folder = self._ctx.folder()
        for speaker in SPEAKERS:
            run.chunkers[speaker] = Chunker(speaker, silence_dbfs=cfg.silence_dbfs, min_s=cfg.min_chunk_s,
                                            max_s=cfg.max_chunk_s, silence_ms=cfg.silence_ms)
            if folder is not None:
                run.recorders[speaker] = WavRecorder(folder / f"{speaker}-{stamp}.wav", self._write_failed, self._write_ok)
        self._run, self.on, self.started_at = run, True, now_ms()
        self.status.problems = {"no_hub": PROBLEM_TEXT["no_hub"]} if not cfg.hub_url.strip() else {}
        self.last_chunk_at = {}
        self.channels = self._planned_channels(cfg)
        self.hinting.start()
        self.summariser.start()
        for target, name in ((self._chunk_worker, "chunks"), (self._transcribe_worker, "transcribe"),
                             (self.hinting.worker, "hints"), (self.summariser.worker, "summary"),
                             (self._open_sources, "sources")):
            threading.Thread(target=target, args=(run,), name=f"copilot-{name}", daemon=True).start()
        if self.live.loop is not None:
            self.live.loop.call_later(TICK_S, self._tick, run)
        logger.info("✅ copilot on (capture %s, %s)", cfg.capture, "local only" if self.local_only else ", ".join(self._ctx.models(run)))
        self.live.event("copilot_on", capture=cfg.capture, local_only=self.local_only)
        self._tell(True)
        self.live.push_state()

    def stop(self) -> None:
        run = self._run
        if not self.on or run is None:
            return
        self._run, self.on = None, False
        self.channels = {}
        self.hinting.clear()
        run.stopped.set()
        self.hinting.end(run)
        run.summaries.put(None)
        threading.Thread(target=self._close_sources, args=(run,), name="copilot-close", daemon=True).start()
        logger.info("ℹ️ copilot off (%d lines, %d hints)", self.status.stats["lines"], self.status.stats["hints"])
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
        return {s: {"label": labels[s], "state": ChannelState.STARTING, "detail": ""} for s in SPEAKERS}

    def _session_changed(self, sid: Optional[str]) -> None:
        self.stop()
        self._forget_transcript()
        self.hinting.forget_session()
        self.last_lag_ms = None
        session = self._load_session() if sid else None
        self.local_only, self.mode, self.interviewee = False, "workshop", ""
        if session is not None:
            self._from_session(session)
        if self.live.folder is not None:
            purge_recordings(self.live.folder, self.config().recording_retention_days)

    def _from_session(self, session: Session) -> None:
        """The presenter's switches as the live session's ``session.yaml`` has them."""
        self.local_only = session.copilot_local_only
        self.mode, self.interviewee = session.copilot_mode, session.copilot_interviewee

    def _reset(self) -> None:
        """The presenter started the session over: the transcript so far went with the old ``live/``."""
        self.hinting.clear()
        self._forget_transcript()

    def _forget_transcript(self) -> None:
        """Drop everything that belongs to the transcript so far; an answer still out is dropped too."""
        self.lines.clear()
        self.status.reset_stats()
        self.last_chunk_at = {}
        self._transcript_log.forget()
        self._ctx.hints_log.forget()
        self.hinting.forget_transcript()
        self.summariser.forget_transcript()

    def sample(self) -> None:
        """Two sample hints on the overlay, Copilot on or off (Settings: where does it sit, how does it look).
        The time rides on the snapshot, so an overlay that starts for it still sees it when it connects."""
        self.sample_at = now_ms()
        self.on_sample()
        self.live.push_state()

    def set_local_only(self, value: bool) -> None:
        """Save the session's local-only switch in ``session.yaml`` (the file first: a failed save changes nothing)."""
        def change(session: Session) -> None:
            session.copilot_local_only = value

        self.live.edit_session(change, "local_only_not_saved", "Local only could not be saved to session.yaml",
                               CopilotError)
        self.local_only = value
        if self._run is not None:
            self._run.local_only = value
        logger.info("ℹ️ copilot: hints from %s", "the local model only" if value else "the configured models")
        self.live.push_state()

    def set_mode(self, mode: str, interviewee: Optional[str] = None) -> None:
        """Save the session's mode (and the interviewee's name; None keeps it) in ``session.yaml``, the file
        first: a failed save changes nothing. While Copilot is on, a fresh hint is asked in the new shape."""
        if self.live.session_id is None:
            raise CopilotError(409, "not_live", "No session is live")
        if mode not in COPILOT_MODES:
            raise CopilotError(422, "unknown_mode", f"Mode must be one of {', '.join(COPILOT_MODES)}")
        name = self.interviewee if interviewee is None else " ".join(interviewee.split())[:INTERVIEWEE_CHARS]

        def change(session: Session) -> None:
            session.copilot_mode, session.copilot_interviewee = mode, name

        self.live.edit_session(change, "mode_not_saved", "The mode could not be saved to session.yaml", CopilotError)
        changed = (mode, name) != (self.mode, self.interviewee)
        self.mode, self.interviewee = mode, name
        logger.info("ℹ️ copilot: %s mode%s", mode, " (interviewee named)" if mode == "interview" and name else "")
        if changed and self.on:
            self.hinting.request("mode")
        self.live.push_state()

    # ----------------------------------------------------------------- capture

    def _sources(self, run: Run, cfg: CopilotConfig) -> list[Any]:
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

    def _open_sources(self, run: Run) -> None:
        for source in self._sources(run, self.config()):
            if run.stopped.is_set():
                return
            try:
                source.start()
            except CaptureError as exc:
                logger.error("❌ copilot: %s", exc)
                self._ctx.on_loop(self._channel, run, source.speaker, ChannelState.ERROR, str(exc))
                continue
            run.sources.append(source)
            self._ctx.on_loop(self._channel, run, source.speaker, ChannelState.RECORDING, "")
        if run.stopped.is_set():
            self._close_sources(run)

    def _channel(self, run: Run, speaker: str, state: ChannelState, detail: str) -> None:
        if run is self._run and speaker in self.channels:
            current = self.channels[speaker]
            self.channels[speaker] = {**current, "state": advance(ChannelState(current["state"]), state), "detail": detail}
            self.live.push_state()

    def _close_sources(self, run: Run) -> None:
        while run.sources:
            run.sources.pop().stop()

    def _write_failed(self, name: str, exc: OSError) -> None:
        self._ctx.on_loop(self._ctx.report_write, name, exc)

    def _write_ok(self, name: str) -> None:
        self._ctx.on_loop(self._ctx.report_write, name, None)

    def _chunk_worker(self, run: Run) -> None:
        while True:
            try:
                speaker, pcm = run.pcm.get(timeout=0.5)
            except queue.Empty:
                if run.stopped.is_set():
                    break
                continue
            run.levels[speaker] = (frame_dbfs(pcm), time.time())
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

    def _queue_chunk(self, run: Run, chunk: Chunk) -> None:
        with run.lock:
            run.seq += 1
            run.pending[run.seq] = chunk.ended_at
            seq = run.seq
        run.chunks.put((seq, chunk))

    # ----------------------------------------------------------- transcription

    def _transcribe_worker(self, run: Run) -> None:
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
            self._ctx.on_loop(self._transcribed, run, chunk, text, failure, time.time())

    def _transcribed(self, run: Run, chunk: Chunk, text: str, failure: Optional[HubFailure], done_at: float) -> None:
        self.status.stats["chunks"] += 1
        if failure is not None:
            code = "hub_down" if isinstance(failure, HubDown) else "transcribe_error"
            self.status.clear("transcribe_error", "hub_down")
            if code not in self.status.problems:
                logger.error("❌ copilot: %s chunk %.0f–%.0f s not transcribed (%s)", chunk.speaker, chunk.t0, chunk.t1, failure)
            self.status.problems[code] = f"{PROBLEM_TEXT[code]} — {failure}"
            self.live.push_state()
            return
        self.status.clear("transcribe_error", "hub_down")
        if run is self._run:
            self.last_chunk_at[chunk.speaker] = now_ms()  # the pipeline answered, even when the chunk held no words
        if not text or NOT_SPEECH.match(text) or run.session_id != self.live.session_id:
            self.live.push_state()
            return
        lag_ms = int((done_at - chunk.ended_at) * 1000)
        self.last_lag_ms = lag_ms
        self._add_line(chunk.speaker, text, chunk.ended_at, {"t0": chunk.t0, "t1": chunk.t1, "lag_ms": lag_ms})
        if chunk.speaker == "them" and run is self._run:
            self.hinting.maybe_request("them")
        self.live.push_state()

    def _add_line(self, speaker: str, text: str, ended_at: float, extra: dict[str, Any]) -> None:
        line = Line(speaker, text, ended_at)
        self.lines.append(line)
        self.summariser.add(line)
        self.status.stats["lines"] += 1
        folder = self._ctx.folder()
        if folder is not None:
            rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), **extra, "speaker": speaker, "text": text}
            self._transcript_log.append(folder / TRANSCRIPT_FILE, [rec])
        self.summariser.maybe_refresh()

    def inject(self, lines: list[tuple[str, str]]) -> int:
        """Lines added as if transcribed (a test, a rehearsal): they always ask for hints at once."""
        if not self.on:
            raise CopilotError(409, "copilot_off", "Switch Copilot on first")
        now = time.time()
        for speaker, text in lines:
            self._add_line(speaker, text, now, {"injected": True})
        self.hinting.request("inject")
        self.live.push_state()
        return len(lines)

    # ------------------------------------------------------------------- hints

    def _item_changed(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        if self.on:
            self.hinting.item_changed()

    def test_hint(self) -> None:
        """One hint check now, labelled a test, on what the facilitator's own channel said: a solo dry run
        shows a real hint end to end without anyone on the other side. Nothing said lately is refused, since
        the model would only see an empty transcript."""
        if not self.on:
            raise CopilotError(409, "copilot_off", "Switch Copilot on first")
        cutoff = time.time() - self.config().context_minutes * 60
        if not any(ln.speaker == "me" and ln.ended_at >= cutoff for ln in self.lines):
            raise CopilotError(409, "nothing_heard", "Say a few sentences into your mic first — the test reads what you said")
        self.hinting.request("test", test=True)
        self.live.push_state()

    # -------------------------------------------------------------------- tick

    def _tick(self, run: Run) -> None:
        """Every ``TICK_S`` while this run is on: the clocks for drift, and a reached cap for room again."""
        if run is not self._run:
            return
        self.hinting.check_drift()
        cfg = self.config()
        changed = False
        for code, limit, per_hour in (("hint_capped", self.hinting.limit, cfg.hint_calls_per_hour),
                                      ("summary_capped", self.summariser.limit, cfg.summary_calls_per_hour)):
            if code in self.status.problems and not limit.full(per_hour):
                self.status.clear(code)
                changed = True
        if changed:
            self.live.push_state()
        if self.live.loop is not None:
            self.live.loop.call_later(TICK_S, self._tick, run)

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        cfg = self.config()
        run = self._run
        behind = 0.0
        if run is not None:
            with run.lock:
                oldest = min(run.pending.values(), default=None)
            behind = max(0.0, time.time() - oldest) if oldest is not None else 0.0
        levels = dict(run.levels) if run is not None else {}
        now = time.time()
        return {
            "on": self.on,
            "consent": self.consented(),
            "started_at": self.started_at if self.on else None,
            "local_only": self.local_only,
            "mode": self.mode,
            "interviewee": self.interviewee,
            "capture": cfg.capture,
            "channels": [_channel_view(s, c, levels.get(s), now) for s, c in self.channels.items()],
            "last_chunk_at": dict(self.last_chunk_at),
            "behind_s": round(behind),
            "behind_warn_s": BEHIND_WARN_S,
            "problems": [{"code": k, "message": v, "level": "warn" if k in WARN_PROBLEMS else "bad"}
                         for k, v in self.status.problems.items()],
            "last_lag_ms": self.last_lag_ms,
            "stats": dict(self.status.stats),
            **self.hinting.snapshot(),
            "sample_at": self.sample_at,
            "overlay": {**asdict(cfg.overlay), "running": self.overlay_running()},
        }
