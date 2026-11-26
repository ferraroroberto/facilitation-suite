"""The rolling summary as it runs: which lines are still to be folded, when the next refresh is due, the call
to the hub on the summary worker's thread and ``live/copilot/summary.json``. The prompt, the answer's parsing
and the call caps are ``summary.py``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from src.atomic_write import atomic_write_text
from src.copilot.context import PROBLEM_TEXT, CopilotContext, Run
from src.copilot.hints import Answer, Line, ask_chain
from src.copilot.hub_client import complete
from src.copilot.summary import SUMMARY_FILE, SUMMARY_MAX_TOKENS, RateLimit, parse_summary, summary_messages

logger = logging.getLogger(__name__)

# A summary is never urgent: it may wait out a slow model, unlike a hint that must land while it still fits.
SUMMARY_TIMEOUT_S = 60


@dataclass
class _SummaryRequest:
    gen: int  # the transcript it summarises: a reset or a new session makes its answer worthless
    lines: list[Line]
    messages: list[dict[str, str]]
    trigger_at: float


class SummaryPipeline:
    def __init__(self, ctx: CopilotContext) -> None:
        self.ctx = ctx
        self.limit = RateLimit()
        self.summary: Optional[dict[str, Any]] = None  # {at, model, text, words}
        self.due = 0.0  # monotonic: no refresh before this
        self._unsummarised: list[Line] = []  # heard since the last refresh
        self._in_flight = False
        self._gen = 0

    @property
    def text(self) -> str:
        return self.summary["text"] if self.summary else ""

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Copilot switched on: pick up this transcript's summary and wait one interval."""
        if self.summary is None:
            self.summary = self._read()
        self.due, self._in_flight = time.monotonic() + self.ctx.config().summary_interval_s, False

    def forget_transcript(self) -> None:
        """The summary belongs to one transcript; an answer still out is dropped."""
        self.summary, self._unsummarised, self._in_flight = None, [], False
        self._gen += 1
        self.ctx.status.clear("summary_failed")

    def _read(self) -> Optional[dict[str, Any]]:
        """The summary kept for this session's transcript (Copilot switched off and on again)."""
        folder = self.ctx.folder()
        path = folder / SUMMARY_FILE if folder is not None else None
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("⚠️ copilot: %s unreadable (%s) — the summary starts again", path, exc)
            return None
        return data if isinstance(data, dict) and isinstance(data.get("text"), str) and data["text"] else None

    # ----------------------------------------------------------------- refresh

    def add(self, line: Line) -> None:
        self._unsummarised.append(line)

    def maybe_refresh(self) -> None:
        """Fold the new lines into the summary once ``summary_interval_s`` has passed since the last refresh."""
        run, cfg = self.ctx.run(), self.ctx.config()
        if (run is None or self._in_flight or not self._unsummarised or not cfg.hub_url.strip()
                or time.monotonic() < self.due):
            return
        if self.ctx.status.capped("summary_capped", self.limit, cfg.summary_calls_per_hour, "summary"):
            self.due = time.monotonic() + cfg.summary_interval_s  # a capped refresh waits its turn again
            return
        lines, self._unsummarised = self._unsummarised, []
        now = time.time()
        messages = summary_messages(run.language, self.text, lines, now, *self.ctx.persona())
        self._in_flight, self.due = True, time.monotonic() + cfg.summary_interval_s
        run.summaries.put(_SummaryRequest(self._gen, lines, messages, now))

    def worker(self, run: Run) -> None:
        while True:
            request = run.summaries.get()
            if request is None:
                return
            cfg = self.ctx.config()
            answer = ask_chain(lambda m, msgs, t, url=cfg.hub_url: complete(url, m, msgs, timeout=t, max_tokens=SUMMARY_MAX_TOKENS),
                               self.ctx.models(run), request.messages, SUMMARY_TIMEOUT_S, parse=parse_summary)
            self.ctx.on_loop(self._answered, run, request, answer, time.time())

    def _answered(self, run: Run, request: _SummaryRequest, answer: Answer, done_at: float) -> None:
        live, status = self.ctx.live, self.ctx.status
        if request.gen != self._gen or run.session_id != live.session_id:
            return  # the transcript it summarised is gone (a reset, another session)
        self._in_flight = False
        lag_ms = int((done_at - request.trigger_at) * 1000)
        folder = self.ctx.folder()
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        if folder is not None:
            rec = {"at": stamp, "reason": "summary", "item_id": None, "model": answer.model, "lag_ms": lag_ms,
                   "hints": [], "lines": len(request.lines), "words": len(answer.text.split()),
                   **({"failure": answer.failure, "detail": answer.detail} if answer.failure else {})}
            self.ctx.log_call(folder, rec)
        if answer.failure:
            self._unsummarised = request.lines + self._unsummarised  # folded in with the next refresh
            logger.warning("⚠️ copilot: summary not refreshed (%s: %s)", answer.failure, answer.detail)
            status.problems["summary_failed"] = f"{PROBLEM_TEXT['summary_failed']} — {answer.detail}"
            live.push_state()
            return
        status.clear("summary_failed")
        self.summary = {"at": stamp, "model": answer.model, "text": answer.text, "words": len(answer.text.split())}
        status.stats["summaries"] += 1
        if folder is not None:
            try:
                atomic_write_text(folder / SUMMARY_FILE, json.dumps(self.summary, ensure_ascii=False, indent=1) + "\n")
                self.ctx.report_write(SUMMARY_FILE, None)
            except OSError as exc:
                self.ctx.report_write(SUMMARY_FILE, exc)
        live.push_state()
