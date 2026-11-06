"""The after-call pass: the recorder's per-side takes transcribed through the hub, and one summary of the call.

The live copilot hears two channels (the mic and the call's loopback) in short chunks while the
session runs. After the call, the podcast recorder's takes (``recorder_tracks.py``) carry each side
at full quality on its own, so this pass transcribes each take on its own and writes, beside the
live log and never into it:

- ``live/copilot/recorder-transcript.jsonl`` — one record per whisper segment, oldest first:
  ``t0``/``t1`` (epoch ms, on the session's clock), ``speaker`` (``me`` / ``them``, the live
  channel names), ``side`` (``host`` / ``guest``), ``name`` (the interviewee's, on a guest line in an
  interview; empty otherwise), ``track`` (the take's file name) and ``text``. Rewritten whole by
  each run, so a second run replaces the first.
- ``live/copilot/recorder-summary.json`` — one summary of the whole recorded transcript, asked of
  the hint models with the session mode's summary prompt: ``{at, model, text, words, lines}``, or
  ``{at, failure, detail, lines}`` when no model gave one.

A take is cut at pauses into chunks of one to three minutes (the hub transcribes five minutes of
audio in about half a minute). The hub down stops the run; a chunk that timed out or failed is
counted as missed and the run goes on, so one bad minute does not cost the hour. The run happens on
its own thread, one at a time per session, and nothing of the audio is kept: the PCM is read
from the take as it is cut and dropped once its chunk is sent.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.atomic_write import atomic_write_text
from src.config import CopilotConfig
from src.copilot.audio import wav_bytes
from src.copilot.hints import NOT_SPEECH, Answer, Line, ask_chain, model_chain
from src.copilot.hub_client import HubDown, HubFailure, HubTimeout, complete, transcribe
from src.copilot.recorder_tracks import Track, TrackError, find_tracks, live_window, pcm_blocks
from src.copilot.summary import SUMMARY_MAX_TOKENS, parse_summary, summary_messages
from src.copilot.vad import SAMPLE_BYTES, SAMPLE_RATE, Chunk, Chunker
from src.errors import DomainError
from src.jsonl import read_jsonl
from src.sessions.model import COPILOT_CONSENT, Session

logger = logging.getLogger(__name__)

RECORDED_TRANSCRIPT_FILE = "recorder-transcript.jsonl"
RECORDED_SUMMARY_FILE = "recorder-summary.json"
# Long chunks give whisper context; three minutes is well inside what the hub took in one upload.
CHUNK_MIN_S = 60.0
CHUNK_MAX_S = 180.0
# Three minutes of audio took the hub about 15 s; the margin covers a dictation queued ahead of it.
CHUNK_TIMEOUT_S = 180
# One summary of the whole call reads far more than a rolling refresh does, so it may take longer.
SUMMARY_BUDGET_S = 180


class AfterCallError(DomainError):
    """A run that cannot start: no consent, no hub, no folder or no takes in it, one already running."""


@dataclass
class Job:
    """The current or last run of one session (kept in memory; the files are what lasts)."""

    state: str  # running | done | failed
    step: str  # transcribe | summary
    started_at: str
    tracks: list[dict[str, Any]] = field(default_factory=list)
    total_s: float = 0.0
    done_s: float = 0.0
    lines: int = 0
    missed: int = 0  # chunks the hub gave no text for; the run went on without them
    missed_detail: str = ""
    failure: str = ""  # why a failed run wrote no transcript
    detail: str = ""
    summary_failure: str = ""  # why the summary step left no summary (the transcript stands)
    summary_detail: str = ""
    finished_at: str = ""


class _Abort(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def copilot_dir(session_folder: Path) -> Path:
    return session_folder / "live" / "copilot"


def read_recorded(session_folder: Path) -> list[dict[str, Any]]:
    """The recorded transcript's lines (empty when there is none)."""
    return read_jsonl(copilot_dir(session_folder) / RECORDED_TRANSCRIPT_FILE)


def read_recorded_summary(session_folder: Path) -> Optional[dict[str, Any]]:
    """The summary of the recorded transcript, or the failure that stands in for it; ``None`` when there is neither."""
    path = copilot_dir(session_folder) / RECORDED_SUMMARY_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ copilot: %s unreadable (%s)", RECORDED_SUMMARY_FILE, exc)
        return None
    return data if isinstance(data, dict) else None


def _segment_lines(track: Track, chunk: Chunk, out: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The hub's answer for one chunk as transcript records on the session's clock."""
    base = track.started + chunk.t0
    length = chunk.t1 - chunk.t0
    segments = [s for s in out.get("segments") or [] if isinstance(s, dict)]
    if not segments:  # an answer without segments still has its text: one line over the chunk
        segments = [{"start": 0.0, "end": length, "text": out.get("text")}]
    lines = []
    for seg in segments:
        text = " ".join(str(seg.get("text") or "").split())
        if not text or NOT_SPEECH.match(text):
            continue
        try:
            start, end = float(seg.get("start") or 0.0), float(seg.get("end") or length)
        except (TypeError, ValueError):
            start, end = 0.0, length
        start, end = min(max(start, 0.0), length), min(max(end, start), length)
        lines.append({"t0": int((base + start) * 1000), "t1": int((base + end) * 1000), "speaker": track.speaker,
                      "side": track.side, "name": name, "track": track.path.name, "text": text})
    return lines


def transcribe_tracks(tracks: list[Track], cfg: CopilotConfig, language: str, interviewee: str,
                      progress: Callable[[float, int], None]) -> tuple[list[dict[str, Any]], int, str, str]:
    """Every take transcribed chunk by chunk: the lines in time order, the chunks missed and the last miss's code
    and detail. ``progress`` is told the seconds of audio read so far and the chunks missed. The hub down, or a
    take that cannot be read, raises ``_Abort``."""
    lines: list[dict[str, Any]] = []
    missed, last_code, last_detail, read_s = 0, "", "", 0.0
    for track in tracks:
        name = interviewee if track.speaker == "them" else ""
        chunker = Chunker(track.speaker, silence_dbfs=cfg.silence_dbfs, min_s=CHUNK_MIN_S, max_s=CHUNK_MAX_S,
                          silence_ms=cfg.silence_ms)

        def send(chunk: Chunk, track: Track = track, name: str = name) -> None:
            nonlocal missed, last_code, last_detail
            try:
                out = transcribe(cfg.hub_url, wav_bytes(chunk.pcm), model=cfg.transcribe_model, language=language,
                                 timeout=CHUNK_TIMEOUT_S)
            except HubDown as exc:
                raise _Abort("hub_down", str(exc)) from exc
            except HubFailure as exc:
                missed += 1
                last_code, last_detail = ("timeout" if isinstance(exc, HubTimeout) else "error"), str(exc)
                logger.warning("⚠️ after-call: %s %.0f–%.0f s not transcribed (%s)", track.path.name, chunk.t0, chunk.t1, exc)
                return
            lines.extend(_segment_lines(track, chunk, out, name))

        try:
            for block in pcm_blocks(track):
                read_s += len(block) / (SAMPLE_RATE * SAMPLE_BYTES)
                for chunk in chunker.feed(block):
                    send(chunk)
                progress(read_s, missed)
        except TrackError as exc:
            raise _Abort(exc.code, str(exc)) from exc
        for chunk in chunker.flush():
            send(chunk)
        progress(read_s, missed)
        logger.info("ℹ️ after-call: %s transcribed (%d lines so far, %d chunks missed)", track.path.name, len(lines), missed)
    lines.sort(key=lambda ln: (ln["t0"], ln["t1"]))
    return lines, missed, last_code, last_detail


def summarise(lines: list[dict[str, Any]], cfg: CopilotConfig, session: Session) -> Answer:
    """One summary of the whole recorded transcript, asked with the session mode's summary prompt."""
    said = [Line(str(ln["speaker"]), str(ln["text"]), ln["t1"] / 1000) for ln in lines]
    messages = summary_messages(session.language, "", said, said[-1].ended_at, session.copilot_mode,
                                session.copilot_interviewee)
    models = model_chain(cfg.hint_models, cfg.local_model, session.copilot_local_only)
    return ask_chain(lambda m, msgs, t: complete(cfg.hub_url, m, msgs, timeout=t, max_tokens=SUMMARY_MAX_TOKENS),
                     models, messages, SUMMARY_BUDGET_S, parse=parse_summary)


def _summary_to_file(session_folder: Path, session: Session, cfg: CopilotConfig, lines: list[dict[str, Any]],
                     again: bool) -> tuple[str, str]:
    """Summarise ``lines`` into ``recorder-summary.json``; the failure code and detail, empty when it worked.

    A fresh transcript's summary replaces the old one either way, since that one was of other lines; asking
    ``again`` keeps a summary already there when the new attempt fails."""
    if not lines:
        failure, detail, record = "empty", "nothing was said in the recorder tracks", None
    else:
        answer = summarise(lines, cfg, session)
        failure, detail = answer.failure or "", answer.detail if answer.failure else ""
        record = None if failure else {"at": _stamp(), "model": answer.model, "text": answer.text,
                                       "words": len(answer.text.split()), "lines": len(lines)}
    if failure:
        logger.warning("⚠️ after-call: no summary (%s: %s)", failure, detail)
        kept = read_recorded_summary(session_folder) if again else None
        if not (kept and kept.get("text")):
            record = {"at": _stamp(), "failure": failure, "detail": detail, "lines": len(lines)}
    if record is not None:
        try:
            atomic_write_text(copilot_dir(session_folder) / RECORDED_SUMMARY_FILE,
                              json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        except OSError as exc:
            logger.error("❌ after-call: %s could not be written (%s)", RECORDED_SUMMARY_FILE, exc)
            return "write_failed", f"{RECORDED_SUMMARY_FILE} could not be written ({exc})"
    if not failure:
        logger.info("✅ after-call: %s written", RECORDED_SUMMARY_FILE)
    return failure, detail


class AfterCall:
    """The after-call runs, one at a time per session, each on its own thread."""

    def __init__(self, config: Callable[[], CopilotConfig]) -> None:
        self.config = config
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def job(self, sid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(sid)
            return asdict(job) if job is not None else None

    def _update(self, sid: str, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._jobs[sid], key, value)

    def _claim(self, sid: str, job: Job) -> None:
        with self._lock:
            current = self._jobs.get(sid)
            if current is not None and current.state == "running":
                raise AfterCallError(409, "running", "The recorder tracks are already being transcribed")
            self._jobs[sid] = job

    def _check(self, session: Session) -> CopilotConfig:
        if not session.checklist.get(COPILOT_CONSENT):
            raise AfterCallError(409, "consent_required", "Confirm the participants' consent to the copilot first")
        cfg = self.config()
        if not cfg.hub_url.strip():
            raise AfterCallError(409, "no_hub", "No hub configured (copilot.hub_url)")
        return cfg

    def start(self, sid: str, session_folder: Path, session: Session) -> None:
        """Transcribe the takes in the session's ``copilot_recorder_folder``, then summarise them."""
        cfg = self._check(session)
        folder_text = session.copilot_recorder_folder.strip()
        if not folder_text:
            raise AfterCallError(422, "no_folder", "Type the recorder's episode folder first")
        folder = Path(folder_text).expanduser()
        if not folder.is_dir():
            raise AfterCallError(422, "folder_missing", f"No folder at {folder}")
        tracks = find_tracks(folder, live_window(read_jsonl(session_folder / "live" / "events.jsonl")))
        if not tracks:
            raise AfterCallError(422, "no_tracks", "No recorder tracks in that folder from while this session was live")
        job = Job("running", "transcribe", _stamp(), tracks=[t.as_dict() for t in tracks],
                  total_s=round(sum(t.duration_s for t in tracks), 1))
        self._claim(sid, job)
        logger.info("ℹ️ after-call: %d recorder take(s), %.0f s of audio, for session %s", len(tracks), job.total_s, sid)
        threading.Thread(target=self._run, args=(sid, session_folder, session, cfg, tracks), daemon=True,
                         name=f"after-call-{sid}").start()

    def start_summary(self, sid: str, session_folder: Path, session: Session) -> None:
        """Ask for the summary again over the recorded transcript already on disk."""
        cfg = self._check(session)
        lines = read_recorded(session_folder)
        if not lines:
            raise AfterCallError(409, "no_transcript", "There is no recorded transcript to summarise")
        self._claim(sid, Job("running", "summary", _stamp(), lines=len(lines)))
        threading.Thread(target=self._run_summary, args=(sid, session_folder, session, cfg, lines, True), daemon=True,
                         name=f"after-call-summary-{sid}").start()

    def _run(self, sid: str, session_folder: Path, session: Session, cfg: CopilotConfig, tracks: list[Track]) -> None:
        try:
            lines, missed, code, detail = transcribe_tracks(
                tracks, cfg, session.language, session.copilot_interviewee,
                lambda done_s, missed: self._update(sid, done_s=round(done_s, 1), missed=missed))
            if missed and not lines:
                raise _Abort(code, f"no chunk was transcribed: {detail}")
            text = "".join(json.dumps(ln, ensure_ascii=False) + "\n" for ln in lines)
            try:
                atomic_write_text(copilot_dir(session_folder) / RECORDED_TRANSCRIPT_FILE, text)
            except OSError as exc:
                raise _Abort("write_failed", f"{RECORDED_TRANSCRIPT_FILE} could not be written ({exc})") from exc
        except _Abort as exc:
            logger.error("❌ after-call: transcript not written (%s: %s)", exc.code, exc.detail)
            self._update(sid, state="failed", failure=exc.code, detail=exc.detail, finished_at=_stamp())
            return
        except Exception as exc:  # a thread has no caller to raise to: the job must still end
            logger.exception("❌ after-call: run failed")
            self._update(sid, state="failed", failure="error", detail=str(exc), finished_at=_stamp())
            return
        logger.info("✅ after-call: %s written, %d lines, %d chunks missed", RECORDED_TRANSCRIPT_FILE, len(lines), missed)
        self._update(sid, lines=len(lines), missed=missed, missed_detail=f"{code}: {detail}" if missed else "")
        self._run_summary(sid, session_folder, session, cfg, lines, False)

    def _run_summary(self, sid: str, session_folder: Path, session: Session, cfg: CopilotConfig,
                     lines: list[dict[str, Any]], again: bool) -> None:
        self._update(sid, step="summary")
        try:
            failure, detail = _summary_to_file(session_folder, session, cfg, lines, again)
        except Exception as exc:  # a thread has no caller to raise to: the job must still end
            logger.exception("❌ after-call: summary failed")
            failure, detail = "error", str(exc)
        self._update(sid, state="done", summary_failure=failure, summary_detail=detail, finished_at=_stamp())

    def status(self, sid: str, session_folder: Path, session: Session) -> dict[str, Any]:
        """What the Results card shows: the saved folder, the consent, the run and what the files hold."""
        path = copilot_dir(session_folder) / RECORDED_TRANSCRIPT_FILE
        try:
            modified_ms: Optional[int] = int(path.stat().st_mtime * 1000)
        except OSError:
            modified_ms = None
        return {"folder": session.copilot_recorder_folder, "consent": bool(session.checklist.get(COPILOT_CONSENT)),
                "job": self.job(sid),
                "transcript": None if modified_ms is None else {"lines": len(read_recorded(session_folder)),
                                                               "modified_ms": modified_ms},
                "summary": read_recorded_summary(session_folder)}
