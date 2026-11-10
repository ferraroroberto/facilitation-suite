"""The live copilot's conversation read back for the Results tab: the transcript next to its hints.

The copilot (``src/copilot/service.py``) leaves two logs in ``live/copilot/``:

- ``transcript.jsonl`` — one record per chunk heard: ``at`` when it was transcribed, ``lag_ms``
  after its audio ended (an injected line has no lag), the ``speaker`` (``me`` / ``them``) and the
  ``text``;
- ``hints.jsonl`` — one record per answer: ``at`` when it came back, the ``item_id`` it was asked
  for, the ``reason``, the ``model``, the ``hints``, and ``failure`` or ``dropped: "stale"`` when
  none of it reached the presenter. A rolling-summary refresh is logged there too and left out here.

After the call the recorder's takes may add ``recorder-transcript.jsonl`` (``src/copilot/after_call.py``):
the same conversation transcribed from each side's own track, each line at ``t0``, when it was said.
Either transcript can be read back with the hints (``source``); the recorded one comes first when both
exist, since it is the better text, and brings its one summary of the call.

A line is placed when it was said (``at`` less its lag) and a hint when it came back; each is then
grouped under an item — a line under the item on stage then (``live/events.jsonl``), a hint under
the item it was asked for, so a stale answer that came back after the item changed still sits
with the item it was about. Nothing here writes; a missing or damaged file reads as empty.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.copilot.after_call import read_recorded, read_recorded_summary
from src.copilot.service import HINTS_FILE, TRANSCRIPT_FILE
from src.importer.review import read_meta
from src.jsonl import read_jsonl
from src.live.plan import build_run, one_line
from src.results.hint_status import HintStatus, hint_status, is_failed, is_stale
from src.sessions.model import Session

logger = logging.getLogger(__name__)

# The events that put an item on stage: the session going live on its current item, and every move.
_ON_STAGE_EVENTS = ("session_live", "item")
SUMMARY_REASON = "summary"
# The transcripts a conversation can be read from, the preferred one first.
SOURCES = ("recorded", "live")


def _epoch_ms(value: Any) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _on_stage(events: list[dict[str, Any]]) -> list[tuple[int, Optional[str]]]:
    """When each item came on stage, oldest first."""
    moves = [(_epoch_ms(e.get("at")), e.get("item_id")) for e in events if e.get("event") in _ON_STAGE_EVENTS]
    return sorted((at, item) for at, item in moves if at is not None)


def _item_at(moves: list[tuple[int, Optional[str]]], at_ms: int) -> Optional[str]:
    item = None
    for when, item_id in moves:
        if when > at_ms:
            break
        item = item_id
    return item


def _line(rec: dict[str, Any], moves: list[tuple[int, Optional[str]]]) -> Optional[dict[str, Any]]:
    at = _epoch_ms(rec.get("at"))
    if at is None or not str(rec.get("text") or "").strip():
        return None
    said = at - int(rec.get("lag_ms") or 0)
    return {"kind": "line", "at_ms": said, "item_id": _item_at(moves, said), "speaker": str(rec.get("speaker") or ""),
            "text": str(rec["text"]).strip(), "injected": bool(rec.get("injected"))}


def _recorded_line(rec: dict[str, Any], moves: list[tuple[int, Optional[str]]]) -> Optional[dict[str, Any]]:
    at = rec.get("t0")
    if not isinstance(at, int) or not str(rec.get("text") or "").strip():
        return None
    return {"kind": "line", "at_ms": at, "item_id": _item_at(moves, at), "speaker": str(rec.get("speaker") or ""),
            "text": str(rec["text"]).strip(), "injected": False}


def _hint(rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    at = _epoch_ms(rec.get("at"))
    if at is None:
        return None
    hints = [{"type": str(h.get("type") or ""), "text": str(h.get("text") or "")}
             for h in rec.get("hints") or [] if isinstance(h, dict) and str(h.get("text") or "").strip()]
    status = hint_status({**rec, "hints": hints})
    return {"kind": "hint", "at_ms": at, "item_id": rec.get("item_id"), "reason": str(rec.get("reason") or ""),
            "model": str(rec.get("model") or ""), "lag_ms": rec.get("lag_ms"), "hints": hints,
            "status": status.value, "failed": is_failed(status), "stale": is_stale(status),
            "failure": str(rec.get("failure") or "")}


def load_conversation(folder: Path, session: Session, source: Optional[str] = None) -> dict[str, Any]:
    """A transcript and the hints in the order they happened, in runs under the item they belong to.

    ``source`` picks the transcript (``recorded`` or ``live``); left out, the first of ``SOURCES`` that has
    lines. ``sources`` lists the ones that have lines. ``segments`` is empty when the copilot never ran in this
    session (or left nothing readable). ``mode`` and ``interviewee`` are the session's, so an interview names
    the other side; ``summary`` is the recorded transcript's summary, read with that transcript only.
    """
    copilot = folder / "live" / "copilot"
    moves = _on_stage(read_jsonl(folder / "live" / "events.jsonl"))
    raw = {"recorded": read_recorded(folder), "live": read_jsonl(copilot / TRANSCRIPT_FILE)}
    read = {"recorded": _recorded_line, "live": _line}
    parsed = {name: [ln for ln in (read[name](r, moves) for r in raw[name]) if ln is not None] for name in SOURCES}
    sources = [name for name in SOURCES if parsed[name]]
    chosen = source if source in SOURCES else (sources[0] if sources else "live")
    raw_lines, lines = raw[chosen], parsed[chosen]
    # A rolling-summary refresh is logged beside the hints (``reason: summary``) but is no hint and has no item.
    raw_hints = [r for r in read_jsonl(copilot / HINTS_FILE) if r.get("reason") != SUMMARY_REASON]
    hints = [h for h in (_hint(r) for r in raw_hints) if h is not None]
    skipped = len(raw_lines) + len(raw_hints) - len(lines) - len(hints)
    if skipped:
        logger.warning("⚠️ copilot conversation: %d record(s) without a time or text left out", skipped)
    # The sort is stable: two records at the same millisecond keep the order they were written in.
    entries = sorted([*lines, *hints], key=lambda e: e["at_ms"])

    titles = {it["id"]: one_line(it["title"]) for it in build_run(session, read_meta(folder / "slides" / "slides.json"))["items"]}
    segments: list[dict[str, Any]] = []
    for entry in entries:
        item_id = entry.pop("item_id")
        if not segments or segments[-1]["item_id"] != item_id:
            segments.append({"item_id": item_id, "title": titles.get(item_id or "", ""), "in_plan": item_id in titles,
                             "entries": []})
        segments[-1]["entries"].append(entry)
    return {"lines": len(lines), "hints": sum(len(h["hints"]) for h in hints if h["status"] == HintStatus.SHOWN),
            "stale": sum(1 for h in hints if h["stale"]),
            "failed": sum(1 for h in hints if h["failed"]), "segments": segments,
            "mode": session.copilot_mode, "interviewee": session.copilot_interviewee, "source": chosen,
            "sources": sources, "summary": read_recorded_summary(folder) if chosen == "recorded" else None}
