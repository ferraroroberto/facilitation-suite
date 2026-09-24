"""The server's side of the chat: messages in, reader health, live pushes.

Messages come from the reader process (Zoom or the simulator) over loopback
HTTP. While a session is live they are appended to its ``live/chat.jsonl``
(one JSON object per line, append-only, epic §5.1) and pushed to every view
as ``{"type": "chat", "messages": [...]}``; the full list is loaded back from
the file when the session goes live again, so a restart loses nothing.

Reader health is its own small state machine, reported on the presenter as
one chip: ``off`` (no reader), ``reading``, ``window_not_found``, ``error``,
``simulating`` — as the reader reports them — and ``stale``, decided here
when the last heartbeat is more than 3 s old. A monitor task re-evaluates it
every second so a dead reader turns the chip red without anyone asking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.chat.parse import Row, new_rows
from src.live.hub import LiveHub, now_ms

logger = logging.getLogger(__name__)

CHAT_FILE = "chat.jsonl"
STALE_AFTER_MS = 3000


class ChatHub:
    def __init__(self, live: LiveHub) -> None:
        self.live = live
        self.messages: list[dict[str, Any]] = []
        self._session: Optional[str] = None
        self._seen_batches: deque[str] = deque(maxlen=2000)
        self.beat: dict[str, Any] = {}
        self.last_beat_ms: Optional[int] = None
        self.last_read_ms: Optional[int] = None
        self.tested: Optional[dict[str, Any]] = None
        self._state = "off"
        self.process_running: Callable[[], bool] = lambda: False
        self.listeners: list[Callable[[list[dict[str, Any]]], None]] = []
        live.extra_state.append(self.state_fields)

    # ------------------------------------------------------------- session

    def _file(self) -> Optional[Path]:
        return self.live.folder / "live" / CHAT_FILE if self.live.folder is not None else None

    def sync_session(self) -> None:
        """Follow the live session: load its chat.jsonl when it changes."""
        sid = self.live.session_id
        if sid == self._session:
            return
        self._session = sid
        self.messages = []
        path = self._file()
        if path is None or not path.is_file():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.messages.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("❌ chat: %s unreadable (%s) — starting with what could be read", CHAT_FILE, exc)
        logger.info("ℹ️ chat: %d messages loaded for session %s", len(self.messages), sid)

    def _append_file(self, records: list[dict[str, Any]]) -> None:
        path = self._file()
        if path is None or not records:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.live._write_failed(CHAT_FILE, exc)

    # ------------------------------------------------------------ messages

    def ingest(self, batch: str, rows: list[dict[str, Any]], *, baseline: bool, source: str) -> list[dict[str, Any]]:
        """Store the new rows of one reader batch; returns the stored records."""
        self.sync_session()
        if batch and batch in self._seen_batches:
            return []
        if batch:
            self._seen_batches.append(batch)
        incoming = [Row(str(r.get("sender", "")), str(r.get("text", "")), str(r.get("time", ""))) for r in rows]
        if baseline:
            # What is already in the window when a reader starts is history; only
            # rows that line up *after* what this session stored are new.
            same = [Row(m["sender"], m["text"], m["time"]) for m in self.messages if m.get("source") == source]
            fresh = new_rows(same, incoming)[0] if same else []
            logger.info("ℹ️ chat: baseline of %d rows from %s — %d new after the stored ones", len(incoming), source, len(fresh))
            received = now_ms()
            pairs = [(r, received) for r in fresh]
        else:
            pairs = [(r, int(raw.get("received_at") or now_ms())) for r, raw in zip(incoming, rows, strict=True)]
        records = []
        for r, received in pairs:
            rec = {"id": len(self.messages) + 1, "sender": r.sender, "text": r.text, "time": r.time,
                   "received_at": received, "at": datetime.now(UTC).isoformat(timespec="milliseconds"), "source": source,
                   # Zoom shows the facilitator's own messages as "You"; they are never answers.
                   "own": r.sender == "You"}
            self.messages.append(rec)
            records.append(rec)
        if records:
            self.last_read_ms = now_ms()
            self._append_file(records)
            for fn in self.listeners:
                fn(records)
            self.live.broadcast({"type": "chat", "messages": records, "count": len(self.messages)})
        return records

    def since(self, after_id: int = 0) -> list[dict[str, Any]]:
        self.sync_session()
        return [m for m in self.messages if m["id"] > after_id]

    # -------------------------------------------------------------- health

    def heartbeat(self, payload: dict[str, Any]) -> None:
        self.beat = payload
        self.last_beat_ms = now_ms()
        if payload.get("state") == "reading":
            self.last_read_ms = self.last_beat_ms
            # A tiny push each second keeps "updated … s ago" honest on the presenter.
            self.live.broadcast({"type": "reader_beat", "last_read_ms": self.last_read_ms})
            if self.tested is None:
                self.tested = {"at": self.last_beat_ms, "rows": payload.get("rows", 0)}
                logger.info("✅ chat reader test passed: the popped-out chat was read (%s rows)", payload.get("rows"))
        self.refresh()

    def reader_state(self) -> tuple[str, str]:
        age = now_ms() - self.last_beat_ms if self.last_beat_ms else None
        reported = self.beat.get("state") if self.beat else None
        if reported == "stopped" or (age is None and not self.process_running()):
            return "off", "The chat reader is not running"
        if age is None:
            return "starting", "The chat reader is starting"
        if age > STALE_AFTER_MS:
            if not self.process_running():
                return "off", "The chat reader stopped"
            return "stale", f"No word from the chat reader for {age // 1000} s"
        return str(reported or "starting"), str(self.beat.get("detail") or "")

    def refresh(self) -> None:
        """Push a snapshot when the reader's state changed."""
        state = self.reader_state()[0]
        if state != self._state:
            logger.info("ℹ️ chat reader: %s → %s", self._state, state)
            self._state = state
            self.live.rev += 1
            self.live.broadcast(self.live.snapshot())

    async def monitor(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 — the monitor must outlive any one bad tick
                logger.exception("❌ chat monitor tick failed")

    def state_fields(self) -> dict[str, Any]:
        state, detail = self.reader_state()
        self.sync_session()
        return {"reader": {"state": state, "detail": detail, "source": self.beat.get("source"),
                           "last_read_ms": self.last_read_ms, "pending": self.beat.get("pending", 0)},
                "chat_count": len(self.messages)}

    def readiness(self) -> dict[str, Any]:
        if self.tested:
            at = time.strftime("%H:%M", time.localtime(self.tested["at"] / 1000))
            return {"tested": True, "detail": f"Read the popped-out chat at {at} ({self.tested['rows']} messages in it)"}
        state, detail = self.reader_state()
        return {"tested": False, "detail": detail if state != "off" else ""}
