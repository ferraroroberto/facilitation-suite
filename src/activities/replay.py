"""Replay a recorded session's captures offline: ``python -m src.activities.replay <session-folder>``.

Reads a session folder's ``session.yaml`` and ``live/`` (``captures.json``,
``chat.jsonl``, ``captures/<item>.json``) and **never writes to it**. Each
captured activity's messages are selected exactly as the live capture did
(``src.live.capture.select_messages``), parsed and aggregated again with the
item's options plus any ``--set key=value`` overrides, and written as
``<item>.json`` (the frozen record's shape) beside ``<item>.compare.json``: the
result against the frozen one — entries added, removed and merged, count
changes, and the answers the result does not show whole. The session's excluded
senders (``--exclude`` adds more) are left out as live does, and
``stop_capture_on_leave`` (``--stop-on-leave`` forces it) cuts each window where
the activity was left, its timer ended with ``hide`` or the session closed, as
live would have stopped it. ``--drop-reposts`` leaves out the rows the reader
posted again as new when the Zoom chat window came back (``reposted_ids``).
``--render`` draws each result through the real stage (``src.activities.replay_render``).

Outputs hold participants' answers, so they go under the gitignored
``data/replay/<folder-name>/`` by default and never into the repo.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from src.activities.registry import counted_for, defaults, result_for
from src.chat.hub import minute_of_day
from src.config import data_dir
from src.importer.review import read_meta
from src.jsonl import read_jsonl
from src.live.capture import STATE_FILE, frozen_answers, select_messages
from src.live.plan import build_run
from src.logger import configure_logging
from src.results.aliases import Excluded, fold, read_aliases
from src.sessions.model import parse_session
from src.sessions.store import SESSION_FILE

logger = logging.getLogger("replay")

# Zoom prints the minute a message was typed; a row received later than this after it is a slow
# read at the very most, not the live feed (the reader scans in seconds).
REPOST_LAG_MINUTES = 1


def reposted_ids(chat: list[dict[str, Any]]) -> set[int]:
    """The ids of the rows the reader posted again as new: received more than ``REPOST_LAG_MINUTES``
    after the time Zoom printed on them, with a row of the same text (case and accents folded) and
    Zoom time stored earlier. The sender is left out of the match, as the live reader's fallback does:
    a participant renamed in Zoom (or merged since) has the window's history relabelled, and a row
    this late is never a second person typing the same word in the same minute."""
    seen: set[tuple[str, Any]] = set()
    out: set[int] = set()
    for m in sorted(chat, key=lambda m: m["id"]):
        key = (fold(str(m.get("text") or "")), m.get("time"))
        printed = minute_of_day(str(m.get("time") or ""))
        if key in seen and printed is not None:
            at = datetime.fromtimestamp(m["received_at"] / 1000)
            if (at.hour * 60 + at.minute - printed) % (24 * 60) > REPOST_LAG_MINUTES:
                out.add(m["id"])
        seen.add(key)
    return out


def load_recording(folder: Path, live_dir: str = "live", *, exclude: tuple[str, ...] = (),
                   stop_on_leave: Optional[bool] = None, drop_reposts: bool = False, plan_options: bool = False) -> dict[str, Any]:
    """Everything the replay reads from a session folder (read-only): the plan's items, the chat,
    each capture's windows, the hidden ids, the places fixed by hand, the frozen records, the events
    and who is excluded (the session's list plus ``exclude``; ``stop_on_leave`` None follows the session)
    and, with ``drop_reposts``, the ids of the rows the reader posted again; ``plan_options`` makes each
    activity use the options the Plan says now instead of the ones it was frozen with."""
    live = folder / live_dir
    state: dict[str, Any] = {}
    if (live / STATE_FILE).is_file():
        state = json.loads((live / STATE_FILE).read_text(encoding="utf-8"))
    frozen: dict[str, dict[str, Any]] = {}
    for path in sorted((live / "captures").glob("*.json")):
        frozen[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    session = parse_session(yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8")))
    items = {it["id"]: it for it in build_run(session, read_meta(folder / "slides" / "slides.json"))["items"]}
    # A folder without captures.json (an older run) still has every frozen record's windows and hidden flags.
    windows = state.get("windows") or {k: f.get("windows") or [] for k, f in frozen.items()}
    hidden = {int(x) for x in state.get("hidden") or []}
    hidden |= {a["id"] for f in frozen.values() for a in f.get("answers") or [] if a.get("hidden")}
    chat = read_jsonl(live / "chat.jsonl")
    return {
        "items": items, "chat": chat, "reposts": reposted_ids(chat) if drop_reposts else set(), "plan_options": plan_options,
        "windows": windows, "hidden": hidden,
        "places": {int(k): str(v) for k, v in (state.get("places") or {}).items()}, "frozen": frozen,
        "events": read_jsonl(live / "events.jsonl"),
        "excluded": Excluded([*session.excluded_senders, *exclude], read_aliases(folder)),
        "stop_on_leave": session.stop_capture_on_leave if stop_on_leave is None else stop_on_leave,
    }


def cut_windows(item_id: str, windows: list[list[Optional[int]]], events: list[dict[str, Any]]) -> list[list[Optional[int]]]:
    """The windows as ``stop_capture_on_leave`` would have left them: each ends at the first move to
    another item, timer end with ``hide`` on this item, or session close after it opened (or earlier
    than it was stopped by hand)."""
    marks = []
    for ev in events:
        name = ev.get("event")
        leaves = (name == "item" and ev.get("item_id") != item_id) or name == "session_closed" or (
            name == "timer_end" and ev.get("item_id") == item_id and ev.get("end") == "hide")
        if leaves and ev.get("at"):
            marks.append(int(datetime.fromisoformat(ev["at"]).timestamp() * 1000))
    marks.sort()
    out: list[list[Optional[int]]] = []
    for start, end in windows:
        cut = next((t for t in marks if start is not None and t > start and (end is None or t < end)), None)
        out.append([start, end if cut is None else cut])
    return out


def parse_sets(pairs: list[str]) -> dict[str, Any]:
    """``["terms=verbatim", "merge_variants=false"]`` → options (JSON values where they parse)."""
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--set wants key=value, got {pair!r}")
        try:
            out[key.strip()] = json.loads(raw)
        except ValueError:
            out[key.strip()] = raw
    return out


def replay_item(rec: dict[str, Any], item_id: str, sets: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One capture recomputed as a frozen record (None: the item is not an activity of the plan)."""
    frozen = rec["frozen"].get(item_id)
    item = dict((frozen or {}).get("item") or rec["items"].get(item_id) or {})
    if rec["plan_options"] and item_id in rec["items"]:
        item["options"] = rec["items"][item_id].get("options") or {}
    if item.get("kind") != "activity" or not item.get("type"):
        return None
    known = defaults(item["type"])
    item["options"] = {**(item.get("options") or {}), **{k: v for k, v in sets.items() if k in known}}
    recorded = rec["windows"].get(item_id) or []
    windows = cut_windows(item_id, recorded, rec["events"]) if rec["stop_on_leave"] else recorded
    in_windows = select_messages(rec["chat"], windows, hidden=rec["hidden"], places=rec["places"], include_hidden=True)
    selected = [m for m in in_windows if m["id"] not in rec["reposts"]]
    counted = [m for m in selected if m["id"] not in rec["hidden"] and m["sender"] not in rec["excluded"]]
    return {
        "item": item, "frozen_at": None, "windows": windows,
        "windows_cut": sum(a != b for a, b in zip(windows, recorded, strict=True)),
        "reposts_dropped": len(in_windows) - len(selected),
        "answers": frozen_answers(item, selected, rec["hidden"], rec["excluded"]),
        "result": result_for(item["type"], item["options"], counted),
        "names": bool((frozen or {}).get("names")),
    }


def _shown(activity_type: str, record: Optional[dict[str, Any]]) -> Optional[dict[int, dict[str, Any]]]:
    """Answer id → what it counts towards in ``record``'s result (None: no record, or a type without ``counted``)."""
    if record is None:
        return None
    out = {}
    for a in record.get("answers") or []:
        if a.get("hidden") or a.get("excluded"):
            continue
        c = counted_for(activity_type, a["text"], a.get("parsed"), record.get("result"))
        if c is None:
            return None
        out[a["id"]] = c
    return out


def compare(frozen: Optional[dict[str, Any]], replayed: dict[str, Any]) -> dict[str, Any]:
    """The replayed result against the frozen one (``frozen`` None: the capture was never frozen)."""
    activity_type = replayed["item"]["type"]
    new_result = json.loads(json.dumps(replayed["result"], ensure_ascii=False))
    answers = [a for a in replayed["answers"] if not a["hidden"] and not a.get("excluded")]
    out: dict[str, Any] = {
        "item_id": replayed["item"]["id"], "type": activity_type, "answers": len(answers),
        "excluded_rows": sum(bool(a.get("excluded")) for a in replayed["answers"]), "windows_cut": replayed.get("windows_cut", 0),
        "reposts_dropped": replayed.get("reposts_dropped", 0),
        "answers_before": None if frozen is None else sum(not a.get("hidden") and not a.get("excluded") for a in frozen.get("answers") or []),
        "frozen": frozen is not None, "same_result": frozen is not None and frozen.get("result") == new_result,
    }
    before, after = _shown(activity_type, frozen), _shown(activity_type, replayed)
    if after is None:
        return out
    out["whole_after"] = sum(c["whole"] for c in after.values())
    out["not_whole"] = [{"id": a["id"], "text": a["text"], "entries": after[a["id"]]["entries"]}
                        for a in answers if not after[a["id"]]["whole"]]
    if before is None:
        return out
    out["whole_before"] = sum(c["whole"] for c in before.values())
    old = {w["key"]: w["count"] for w in (frozen or {}).get("result", {}).get("words") or []}
    new = {w["key"]: w["count"] for w in (new_result or {}).get("words") or []}
    # A gone entry whose answers all went to one entry is merged when that entry gathers two or more old ones.
    into: dict[str, str] = {}
    for key in [k for k in old if k not in new]:
        targets = {e for i, c in before.items() if key in c["entries"] for e in (after.get(i) or {}).get("entries", [])}
        if len(targets) == 1:
            into[key] = targets.pop()
    sources = {t: sum(v == t for v in into.values()) + (t in old) for t in set(into.values())}
    merged = {k: t for k, t in into.items() if sources[t] >= 2}
    removed = [k for k in old if k not in new and k not in merged]
    out.update(added=[k for k in new if k not in old], removed=removed, merged=merged,
               count_changes={k: [old[k], new[k]] for k in old if k in new and old[k] != new[k]})
    return out


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def replay_records(folder: Path, *, items: Optional[list[str]] = None, sets: Optional[dict[str, Any]] = None,
                   live_dir: str = "live", exclude: tuple[str, ...] = (), stop_on_leave: Optional[bool] = None,
                   drop_reposts: bool = False, plan_options: bool = False) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every captured activity (or ``items``) recomputed in plan order, as (replayed record, comparison) pairs; writes nothing."""
    rec = load_recording(folder.resolve(), live_dir, exclude=exclude, stop_on_leave=stop_on_leave,
                         drop_reposts=drop_reposts, plan_options=plan_options)
    order = {k: it["index"] for k, it in rec["items"].items()}
    wanted = items or sorted(rec["windows"], key=lambda k: order.get(k, len(order)))
    pairs = []
    for item_id in wanted:
        replayed = replay_item(rec, item_id, sets or {})
        if replayed is None:
            logger.warning("⚠️ %s: not a captured activity of this session — skipped", item_id)
            continue
        pairs.append((replayed, compare(rec["frozen"].get(item_id), replayed)))
    return pairs


def replay(folder: Path, out: Path, *, items: Optional[list[str]] = None, sets: Optional[dict[str, Any]] = None,
           live_dir: str = "live", exclude: tuple[str, ...] = (), stop_on_leave: Optional[bool] = None,
           drop_reposts: bool = False) -> list[dict[str, Any]]:
    """Replay every captured activity (or ``items``) into ``out``; returns one comparison per item."""
    folder, out = folder.resolve(), out.resolve()
    if out == folder or folder in out.parents:
        raise ValueError("the replay never writes inside the session folder; pick another --out")
    summary = []
    for replayed, cmp in replay_records(folder, items=items, sets=sets, live_dir=live_dir, exclude=exclude,
                                        stop_on_leave=stop_on_leave, drop_reposts=drop_reposts):
        _write(out / f"{cmp['item_id']}.json", replayed)
        _write(out / f"{cmp['item_id']}.compare.json", cmp)
        summary.append(cmp)
    totals = {"excluded_rows": sum(c["excluded_rows"] for c in summary), "windows_cut": sum(c["windows_cut"] for c in summary),
              "reposts_dropped": sum(c["reposts_dropped"] for c in summary), "never_frozen": sum(not c["frozen"] for c in summary)}
    _write(out / "summary.json", {"session": folder.name, "live": live_dir, "set": sets or {}, "totals": totals, "items": summary})
    return summary


def _line(c: dict[str, Any]) -> str:
    whole = f"{c.get('whole_before', '–')}→{c['whole_after']}/{c['answers']} whole" if "whole_after" in c else ""
    diff = f"+{len(c['added'])} -{len(c['removed'])} merged {len(c['merged'])}" if "added" in c else ""
    state = "same" if c["same_result"] else ("changed" if c["frozen"] else "never frozen")
    kept = " ".join(f"{label} {c[key]}" for key, label in (("excluded_rows", "excluded"), ("windows_cut", "cut"), ("reposts_dropped", "reposts")) if c[key])
    return f"{c['item_id']:<14} {c['type']:<12} {c['answers']:>3} answers  {state:<12} {whole:<18} {diff} {kept}".rstrip()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.activities.replay", description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, help="the session folder (read-only)")
    ap.add_argument("--item", action="append", default=[], help="replay only this item id (repeatable)")
    ap.add_argument("--set", action="append", default=[], dest="sets", metavar="KEY=VALUE",
                    help="override one activity option, e.g. terms=verbatim (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], metavar="NAME",
                    help="also leave this chat sender out, as an excluded sender (repeatable)")
    ap.add_argument("--stop-on-leave", action=argparse.BooleanOptionalAction, default=None,
                    help="cut each capture where its activity was left (default: the session's setting)")
    ap.add_argument("--drop-reposts", action="store_true",
                    help="leave out rows the reader posted again: received over a minute after Zoom's time, an identical earlier row")
    ap.add_argument("--live", default="live", help="the folder's live-data subfolder (default: live)")
    ap.add_argument("--out", type=Path, help="output folder (default: data/replay/<folder-name>/)")
    ap.add_argument("--render", action="store_true", help="also draw each result to <item>.png through the real stage")
    args = ap.parse_args(argv)
    configure_logging(to_file=False)
    if not (args.folder / SESSION_FILE).is_file():
        logger.error("❌ %s has no %s", args.folder, SESSION_FILE)
        return 1
    out = args.out or data_dir() / "replay" / args.folder.resolve().name
    try:
        summary = replay(args.folder, out, items=args.item or None, sets=parse_sets(args.sets), live_dir=args.live,
                         exclude=tuple(args.exclude), stop_on_leave=args.stop_on_leave, drop_reposts=args.drop_reposts)
    except ValueError as exc:
        logger.error("❌ %s", exc)
        return 1
    for c in summary:
        logger.info("ℹ️ %s", _line(c))
    logger.info("✅ %d captures replayed into %s: %d rows excluded, %d windows cut, %d reposted rows dropped, %d captures never frozen",
                len(summary), out, sum(c["excluded_rows"] for c in summary), sum(c["windows_cut"] for c in summary),
                sum(c["reposts_dropped"] for c in summary), sum(not c["frozen"] for c in summary))
    if args.render:
        from src.activities.replay_render import render_all

        failed = render_all(args.folder, out, [c["item_id"] for c in summary])
        return 2 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
