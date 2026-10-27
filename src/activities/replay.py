"""Replay a recorded session's captures offline: ``python -m src.activities.replay <session-folder>``.

Reads a session folder's ``session.yaml`` and ``live/`` (``captures.json``,
``chat.jsonl``, ``captures/<item>.json``) and **never writes to it**. Each
captured activity's messages are selected exactly as the live capture did
(``src.live.capture.select_messages``), parsed and aggregated again with the
item's options plus any ``--set key=value`` overrides, and written as
``<item>.json`` (the frozen record's shape) beside ``<item>.compare.json``: the
result against the frozen one — entries added, removed and merged, count
changes, and the answers the result does not show whole. ``--render`` draws
each result through the real stage (``src.activities.replay_render``).

Outputs hold participants' answers, so they go under the gitignored
``data/replay/<folder-name>/`` by default and never into the repo.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from src.activities.registry import counted_for, defaults, result_for
from src.config import data_dir
from src.importer.review import read_meta
from src.jsonl import read_jsonl
from src.live.capture import STATE_FILE, frozen_answers, select_messages
from src.live.plan import build_run
from src.logger import configure_logging
from src.sessions.model import parse_session
from src.sessions.store import SESSION_FILE

logger = logging.getLogger("replay")


def load_recording(folder: Path, live_dir: str = "live") -> dict[str, Any]:
    """Everything the replay reads from a session folder (read-only): the plan's items, the chat,
    each capture's windows, the hidden ids, the places fixed by hand and the frozen records."""
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
    return {
        "items": items, "chat": read_jsonl(live / "chat.jsonl"), "windows": windows, "hidden": hidden,
        "places": {int(k): str(v) for k, v in (state.get("places") or {}).items()}, "frozen": frozen,
    }


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
    if item.get("kind") != "activity" or not item.get("type"):
        return None
    known = defaults(item["type"])
    item["options"] = {**(item.get("options") or {}), **{k: v for k, v in sets.items() if k in known}}
    windows = rec["windows"].get(item_id) or []
    selected = select_messages(rec["chat"], windows, hidden=rec["hidden"], places=rec["places"], include_hidden=True)
    counted = [m for m in selected if m["id"] not in rec["hidden"]]
    return {
        "item": item, "frozen_at": None, "windows": windows,
        "answers": frozen_answers(item, selected, rec["hidden"]),
        "result": result_for(item["type"], item["options"], counted),
        "names": bool((frozen or {}).get("names")),
    }


def _shown(activity_type: str, record: Optional[dict[str, Any]]) -> Optional[dict[int, dict[str, Any]]]:
    """Answer id → what it counts towards in ``record``'s result (None: no record, or a type without ``counted``)."""
    if record is None:
        return None
    out = {}
    for a in record.get("answers") or []:
        if a.get("hidden"):
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
    answers = [a for a in replayed["answers"] if not a["hidden"]]
    out: dict[str, Any] = {
        "item_id": replayed["item"]["id"], "type": activity_type, "answers": len(answers),
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


def replay(folder: Path, out: Path, *, items: Optional[list[str]] = None, sets: Optional[dict[str, Any]] = None,
           live_dir: str = "live") -> list[dict[str, Any]]:
    """Replay every captured activity (or ``items``) into ``out``; returns one comparison per item."""
    folder, out = folder.resolve(), out.resolve()
    if out == folder or folder in out.parents:
        raise ValueError("the replay never writes inside the session folder; pick another --out")
    rec = load_recording(folder, live_dir)
    order = {k: it["index"] for k, it in rec["items"].items()}
    wanted = items or sorted(rec["windows"], key=lambda k: order.get(k, len(order)))
    summary = []
    for item_id in wanted:
        replayed = replay_item(rec, item_id, sets or {})
        if replayed is None:
            logger.warning("⚠️ %s: not a captured activity of this session — skipped", item_id)
            continue
        cmp = compare(rec["frozen"].get(item_id), replayed)
        _write(out / f"{item_id}.json", replayed)
        _write(out / f"{item_id}.compare.json", cmp)
        summary.append(cmp)
    _write(out / "summary.json", {"session": folder.name, "live": live_dir, "set": sets or {}, "items": summary})
    return summary


def _line(c: dict[str, Any]) -> str:
    whole = f"{c.get('whole_before', '–')}→{c['whole_after']}/{c['answers']} whole" if "whole_after" in c else ""
    diff = f"+{len(c['added'])} -{len(c['removed'])} merged {len(c['merged'])}" if "added" in c else ""
    state = "same" if c["same_result"] else ("changed" if c["frozen"] else "never frozen")
    return f"{c['item_id']:<14} {c['type']:<12} {c['answers']:>3} answers  {state:<12} {whole:<18} {diff}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.activities.replay", description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, help="the session folder (read-only)")
    ap.add_argument("--item", action="append", default=[], help="replay only this item id (repeatable)")
    ap.add_argument("--set", action="append", default=[], dest="sets", metavar="KEY=VALUE",
                    help="override one activity option, e.g. terms=verbatim (repeatable)")
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
        summary = replay(args.folder, out, items=args.item or None, sets=parse_sets(args.sets), live_dir=args.live)
    except ValueError as exc:
        logger.error("❌ %s", exc)
        return 1
    for c in summary:
        logger.info("ℹ️ %s", _line(c))
    logger.info("✅ %d captures replayed into %s", len(summary), out)
    if args.render:
        from src.activities.replay_render import render_all

        failed = render_all(args.folder, out, [c["item_id"] for c in summary])
        return 2 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
