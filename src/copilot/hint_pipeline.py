"""The hints on show: when they are asked, how an answer lands, how long they live, and the drift warning.

A hint request waits in the run's one-slot queue (a newer one supersedes it) and is answered on the hint
worker's thread; the drift hint is computed from the clocks on the server's loop, with no model call.
"""

from __future__ import annotations

import logging
import queue
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from src.copilot.context import PROBLEM_TEXT, CopilotContext, Run
from src.copilot.hints import (
    MAX_HINTS,
    MODEL_HINT_TYPES,
    PLAYBOOK_CHARS,
    Answer,
    Line,
    ask_chain,
    clock_state,
    drift_hint,
    drift_now,
    parse_hints,
    speaker_labels,
    system_prompt,
    user_prompt,
)
from src.copilot.hub_client import complete
from src.copilot.summary import RateLimit
from src.live.hub import now_ms
from src.sessions.playbook import PLAYBOOK_FILE, playbook_text

logger = logging.getLogger(__name__)


@dataclass
class _HintRequest:
    seq: int
    item_id: Optional[str]
    reason: str
    messages: list[dict[str, str]]
    trigger_at: float
    test: bool = False  # asked by the presenter's "Test copilot": its hints carry the label


class HintPipeline:
    def __init__(self, ctx: CopilotContext, lines: deque[Line], summary_text: Callable[[], str]) -> None:
        self.ctx = ctx
        self.lines = lines  # the transcript, owned and filled by the service
        self.summary_text = summary_text
        self.limit = RateLimit()
        self.hints: list[dict[str, str]] = []
        self.hints_at: Optional[int] = None
        self.hints_item_id: Optional[str] = None
        self.hints_model = ""
        self.last_hint_ms: Optional[int] = None
        # How the latest hint check ended ({at, outcome, reason, test, model}).
        self.last_check: Optional[dict[str, Any]] = None
        self._playbook_seen: Optional[tuple[tuple[int, int], str]] = None  # ((mtime_ns, size), text) of playbook.md
        self._drift_warned: set[str] = set()  # the sections (or "session") already warned about
        self._seq = 0
        self._shown_seq = 0
        self._last_request = float("-inf")

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Copilot switched on: nothing on show, and the first line may ask at once."""
        self.hints, self.hints_at, self.hints_item_id, self._last_request = [], None, None, float("-inf")
        self.last_check = None

    def clear(self) -> None:
        self.hints, self.hints_at = [], None

    def forget_transcript(self) -> None:
        """The last check and the drift warnings belong to one transcript."""
        self.last_check = None
        self._drift_warned.clear()

    def forget_session(self) -> None:
        self.last_hint_ms = self._playbook_seen = None

    def item_changed(self) -> None:
        self.clear()  # they were for the item that just left
        self.check_drift()
        self.request("item")

    # ------------------------------------------------------------------- hints

    def maybe_request(self, reason: str) -> None:
        """A hint asked at most once per ``hint_interval_s``."""
        if time.monotonic() - self._last_request >= self.ctx.config().hint_interval_s:
            self.request(reason)

    def playbook(self) -> tuple[str, int]:
        """The session's playbook text and its full length (``("", 0)`` without one), re-read when the file changes."""
        folder = self.ctx.live.folder
        try:
            stat = (folder / PLAYBOOK_FILE).stat() if folder is not None else None
        except OSError:
            stat = None
        if stat is None:
            self._playbook_seen = None
            return "", 0
        key = (stat.st_mtime_ns, stat.st_size)
        if self._playbook_seen is None or self._playbook_seen[0] != key:
            self._playbook_seen = (key, playbook_text(folder))
        text = self._playbook_seen[1]
        return text, len(text)

    def request(self, reason: str, test: bool = False) -> None:
        run = self.ctx.run()
        if run is None:
            return
        cfg, live = self.ctx.config(), self.ctx.live
        if self.ctx.status.capped("hint_capped", self.limit, cfg.hint_calls_per_hour, "hint"):
            return
        self._last_request = time.monotonic()
        self._seq += 1
        cur = live.current()
        nxt = next((it for it in live.items[(cur["index"] + 1) if cur else 0:] if not it.get("hidden")), None)
        now = time.time()
        tail = [ln for ln in self.lines if now - ln.ended_at <= cfg.context_minutes * 60]
        clock = clock_state(live.run, cur, live.clock_started_at, live.section_entered, live.duration_minutes, now_ms())
        mode, interviewee = self.ctx.persona()
        messages = [{"role": "system", "content": system_prompt(run.language, mode, interviewee)},
                    {"role": "user", "content": user_prompt(live.session_title, cur, nxt, clock, tail, now,
                                                            self.summary_text(),
                                                            speaker_labels(mode, interviewee),
                                                            self.playbook()[0])}]
        request = _HintRequest(self._seq, cur["id"] if cur else None, reason, messages, now, test)
        try:
            run.hints.get_nowait()  # an older request still waiting is superseded by this one
            self.limit.refund()  # and never reached the hub
        except queue.Empty:
            pass
        run.hints.put_nowait(request)

    def worker(self, run: Run) -> None:
        while True:
            request = run.hints.get()
            if request is None:
                return
            cfg = self.ctx.config()
            if not cfg.hub_url.strip():
                answer = Answer(failure="error", detail=PROBLEM_TEXT["no_hub"])
            else:
                answer = ask_chain(lambda m, msgs, t, url=cfg.hub_url: complete(url, m, msgs, timeout=t),
                                   self.ctx.models(run), request.messages, cfg.hint_timeout_s,
                                   parse=lambda text: parse_hints(text, MODEL_HINT_TYPES))
            self.ctx.on_loop(self._answered, run, request, answer, time.time())

    def _answered(self, run: Run, request: _HintRequest, answer: Answer, done_at: float) -> None:
        live, status = self.ctx.live, self.ctx.status
        current = run is self.ctx.run()
        lag_ms = int((done_at - request.trigger_at) * 1000)
        cur = live.current()
        stale = not current or (cur["id"] if cur else None) != request.item_id or request.seq < self._shown_seq
        folder = self.ctx.folder()
        if folder is not None and run.session_id == live.session_id:
            rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "reason": request.reason,
                   "item_id": request.item_id, "model": answer.model, "lag_ms": lag_ms,
                   "hints": [h.as_dict() for h in answer.hints], **({"failure": answer.failure, "detail": answer.detail} if answer.failure else {}),
                   **({"dropped": "stale"} if stale and not answer.failure else {})}
            self.ctx.log_call(folder, rec)
        if not current:
            return
        status.clear("hint_timeout", "hint_error", "hub_down")  # the latest answer says how the hub is
        outcome = "failed" if answer.failure else "stale" if stale else "shown" if answer.hints else "none"
        self.last_check = {"at": now_ms(), "outcome": outcome, "reason": request.reason, "test": request.test, "model": answer.model}
        if answer.failure:
            code = {"hub_down": "hub_down", "timeout": "hint_timeout"}.get(answer.failure, "hint_error")
            logger.warning("⚠️ copilot: no hint (%s: %s)", code, answer.detail)
            status.problems[code] = f"{PROBLEM_TEXT[code]} — {answer.detail}"
            live.push_state()
            return
        self.last_hint_ms = lag_ms
        logger.info("ℹ️ copilot: hint check (%s) → %s in %d ms", request.reason, outcome, lag_ms)
        if outcome == "shown":
            self._shown_seq = request.seq
            drift = [h for h in self._fresh(request.item_id) if h["type"] == "drift"]  # the clock's warning stays first
            label = {"test": True} if request.test else {}
            self._show([*drift, *({**h.as_dict(), **label} for h in answer.hints)][:MAX_HINTS], request.item_id, answer.model)
            status.stats["hints"] += len(answer.hints)
        live.push_state()

    # ------------------------------------------------------------------ on show

    def _alive(self) -> bool:
        """The hints on show have not yet lived their ``hint_ttl_s``."""
        return self.hints_at is not None and now_ms() - self.hints_at < self.ctx.config().hint_ttl_s * 1000

    def _fresh(self, item_id: Optional[str]) -> list[dict[str, str]]:
        """The hints on show for this item."""
        return list(self.hints) if self._alive() and self.hints_item_id == item_id else []

    def _show(self, hints: list[dict[str, str]], item_id: Optional[str], model: str) -> None:
        self.hints = hints
        self.hints_at, self.hints_item_id, self.hints_model = now_ms(), item_id, model
        loop = self.ctx.live.loop
        if loop is not None:
            loop.call_later(self.ctx.config().hint_ttl_s, self._expire, self.hints_at)

    def _expire(self, at: int) -> None:
        if self.hints_at == at:
            self.clear()
            self.ctx.live.push_state()

    # ------------------------------------------------------------------- drift

    def check_drift(self) -> None:
        """One computed ``drift`` hint the first time the section on stage (or the whole session,
        outside any section) runs ``drift_hint_minutes`` behind the plan. No model is asked."""
        run, cfg, live = self.ctx.run(), self.ctx.config(), self.ctx.live
        if run is None or cfg.drift_hint_minutes <= 0:
            return
        cur = live.current()
        drift = drift_now(live.run, cur, live.clock_started_at, live.section_entered, live.duration_minutes, now_ms())
        if drift is None or drift[2] < cfg.drift_hint_minutes or drift[0] in self._drift_warned:
            return
        key, name, minutes = drift
        self._drift_warned.add(key)
        item_id = cur["id"] if cur else None
        hint = drift_hint(run.language, name, minutes).as_dict()
        others = [h for h in self._fresh(item_id) if h["type"] != "drift"]
        self._show([hint, *others][:MAX_HINTS], item_id, "clock")
        self.ctx.status.stats["hints"] += 1
        logger.info("ℹ️ copilot: %s is %d min behind the plan — drift hint shown", name or "the session", minutes)
        folder = self.ctx.folder()
        if folder is not None:
            rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "reason": "drift", "item_id": item_id,
                   "model": "clock", "lag_ms": 0, "hints": [hint], "drift_min": minutes}
            self.ctx.log_call(folder, rec)
        live.push_state()

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        """The hint part of the copilot's block on the live snapshot."""
        fresh = self._alive()
        playbook_chars = self.playbook()[1]
        return {
            "last_check": self.last_check,
            "playbook": {"exists": playbook_chars > 0, "chars": playbook_chars, "used_chars": min(playbook_chars, PLAYBOOK_CHARS)},
            "hints": self.hints if fresh else [],
            "hints_at": self.hints_at if fresh else None,
            "hints_item_id": self.hints_item_id if fresh else None,
            "hints_model": self.hints_model if fresh else "",
            "ttl_s": self.ctx.config().hint_ttl_s,
            "last_hint_ms": self.last_hint_ms,
        }
