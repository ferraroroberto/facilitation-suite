"""A session's results, read back from its folder after (or during) the session.

Everything comes from files the live session wrote (epic §5.1, §14), so the
Results tab works on any session without it being live:

- ``live/captures/<item>.json`` — each frozen capture: the item, its windows,
  every answer with its name (hidden ones flagged) and the result;
- ``live/captures/<item>.png`` — the stage exactly as it looked at the stop;
- ``live/events.jsonl`` — what was shown when, which gives the session PDF
  its order: every slide at its first showing, each capture at its last stop
  (the frozen image is the one from that stop);
- ``live/chat.jsonl`` — every chat message (participation counts, the Zoom
  reconciliation).

Nothing here writes; a missing or damaged file reads as empty and is logged.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.activities.registry import editors, report_for, value_for
from src.live.plan import build_run, one_line
from src.sessions.model import Session
from src.sessions.readiness import slides_meta

logger = logging.getLogger(__name__)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every parseable line of a JSONL file; a damaged line is skipped and logged."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.error("❌ results: %s unreadable (%s)", path.name, exc)
        return []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("⚠️ results: %s line %d is not JSON — skipped", path.name, n)
    return out


def frozen_captures(folder: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    cap_dir = folder / "live" / "captures"
    if not cap_dir.is_dir():
        return out
    for f in sorted(cap_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ results: capture %s unreadable (%s) — left out", f.name, exc)
            continue
        if isinstance(data, dict) and isinstance(data.get("item"), dict):
            out[f.stem] = data
    return out


def hhmm(ms: Optional[int]) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M") if ms else ""


def _activity(item_id: str, frozen: dict[str, Any], png: Path) -> dict[str, Any]:
    item = frozen["item"]
    kind = item.get("type") or ""
    rep = report_for(kind, frozen.get("result"))
    answers = frozen.get("answers") or []
    counted = [a for a in answers if not a.get("hidden")]
    windows = [w for w in frozen.get("windows") or [] if w and w[0] is not None]
    start = windows[0][0] if windows else None
    stop = windows[-1][1] if windows else None
    return {
        "id": item_id,
        "type": kind,
        "type_label": item.get("type_label") or (editors().get(kind) or {}).get("label") or kind,
        "title": one_line(item.get("title") or item.get("question") or "Activity"),
        "question": item.get("question") or "",
        "answers": len(counted),
        "people": len({a["sender"] for a in counted if a.get("sender")}),
        "hidden": len(answers) - len(counted),
        "start_ms": start,
        "stop_ms": stop,
        "captured": f"{hhmm(start)}–{hhmm(stop)}" if start and stop and hhmm(start) != hhmm(stop) else hhmm(start),
        "frozen_at": frozen.get("frozen_at"),
        "has_png": png.is_file(),
        **rep,
        "answer_rows": [{
            "id": a.get("id"), "sender": a.get("sender", ""), "text": a.get("text", ""), "time": a.get("time", ""),
            "hidden": bool(a.get("hidden")), "value": value_for(kind, a.get("parsed")),
        } for a in answers],
    }


def timeline(events: list[dict[str, Any]], run_items: list[dict[str, Any]], captured: set[str]) -> list[dict[str, Any]]:
    """The session PDF's pages in the order they happened.

    A slide is a page at its first showing; a capture is a page at its last
    stop. Items shown that are not slides (activities, breaks) only count
    through their captures. A slide no longer in the plan is left out.
    """
    by_id = {it["id"]: it for it in run_items}
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    live_once = False

    def shown(item_id: Optional[str], at: str) -> None:
        it = by_id.get(item_id or "")
        if it is None or it["kind"] != "slide" or not it.get("slide_file") or item_id in seen:
            return
        seen.add(item_id)
        pages.append({"kind": "slide", "item_id": item_id, "title": one_line(it["title"]), "file": it["slide_file"], "at": at})

    for ev in events:
        name, at = ev.get("event"), ev.get("at", "")
        if name == "session_live":
            if ev.get("item_id"):
                shown(ev["item_id"], at)
            elif not live_once and run_items:
                shown(run_items[0]["id"], at)  # before the event carried its item: a first start is on item 1
            live_once = True
        elif name == "item":
            shown(ev.get("item_id"), at)
        elif name == "capture_stop" and ev.get("item_id") in captured:
            pages[:] = [p for p in pages if not (p["kind"] == "capture" and p["item_id"] == ev["item_id"])]
            pages.append({"kind": "capture", "item_id": ev["item_id"], "at": at})
    return pages


def load_results(folder: Path, session: Session) -> dict[str, Any]:
    """Everything the Results tab, the PDF and the Excel report read."""
    captures = frozen_captures(folder)
    events = read_jsonl(folder / "live" / "events.jsonl")
    run = build_run(session, slides_meta(folder))
    cap_dir = folder / "live" / "captures"
    acts = {iid: _activity(iid, fr, cap_dir / f"{iid}.png") for iid, fr in captures.items()}
    pages = timeline(events, run["items"], set(acts))
    # Activities in the order they happened (their page), then any capture without a stop event.
    order = [p["item_id"] for p in pages if p["kind"] == "capture"]
    order += sorted((i for i in acts if i not in order), key=lambda i: acts[i]["start_ms"] or 0)
    activities = [acts[i] for i in order]
    for n, a in enumerate(activities, start=1):
        a["number"] = n
    for p in pages:
        if p["kind"] == "capture":
            a = acts[p["item_id"]]
            p.update(title=a["title"], label=f"Live {a['type_label'].lower()}", tile=a["tile"], has_png=a["has_png"])
    return {
        "session": {"title": session.title, "date": session.date.isoformat() if session.date else None},
        "activities": activities,
        "pages": pages,
        "slides": sum(1 for p in pages if p["kind"] == "slide"),
        "captures": sum(1 for p in pages if p["kind"] == "capture"),
        "went_live": any(e.get("event") == "session_live" for e in events),
    }


def participation(activities: list[dict[str, Any]], chat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per person: counted answers per activity, their total, and chat messages sent."""
    people: dict[str, dict[str, Any]] = {}

    def row(name: str) -> dict[str, Any]:
        return people.setdefault(name, {"name": name, "total": 0, "messages": 0, "by_activity": {}})

    for a in activities:
        for ans in a["answer_rows"]:
            if ans["hidden"] or not ans["sender"]:
                continue
            r = row(ans["sender"])
            r["by_activity"][a["id"]] = r["by_activity"].get(a["id"], 0) + 1
            r["total"] += 1
    for m in chat:
        if not m.get("own") and m.get("sender"):
            row(m["sender"])["messages"] += 1
    return sorted(people.values(), key=lambda r: (-r["total"], -r["messages"], r["name"].casefold()))
