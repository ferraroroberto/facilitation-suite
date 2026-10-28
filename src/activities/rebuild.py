"""Rebuild a closed session's captures with the rules and options of today.

Preview, apply and undo over the offline replay (``src.activities.replay``), so
selection, parsing and aggregation are the live capture's, not a second copy:

- ``preview`` recomputes every frozen capture and reports what would change,
  per activity; it writes nothing;
- ``apply`` recomputes, draws the new pictures through the real stage into a
  scratch folder first, then backs up ``live/captures/`` and ``captures.json``
  into ``live/rebuild-backup-<time>/`` and swaps the rebuilt records and
  pictures in; a failed render changes nothing;
- ``undo`` puts the latest backup back, file for file.

A rebuild uses each activity's options as the Plan says now, the session's
``stop_capture_on_leave`` (a session that never set it is not cut: cutting
drops real late answers) and leaves out the rows the reader posted again
(``replay.reposted_ids``). It never touches the chat, the events or the
exports: the owner exports the PDF and the Excel again afterwards.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.activities.replay import replay_records
from src.activities.replay_render import render_all
from src.config import data_dir
from src.errors import DomainError
from src.live.capture import STATE_FILE
from src.live.plan import one_line
from src.sessions.store import atomic_write_text

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "rebuild-backup-"
UNDONE_PREFIX = "rebuild-undone-"
RECORD_KEYS = ("item", "frozen_at", "windows", "answers", "result", "names")  # a frozen record, as the live freeze writes it
_busy = threading.Lock()  # a rebuild draws for tens of seconds: one at a time


class RebuildError(DomainError):
    """A rebuild that cannot run or did not finish (nothing is left half-applied)."""


def _live(folder: Path) -> Path:
    return folder / "live"


def backups(folder: Path) -> list[str]:
    """The backup folders in ``live/``, oldest first (the name carries the time)."""
    try:
        return sorted(p.name for p in _live(folder).iterdir() if p.is_dir() and p.name.startswith(BACKUP_PREFIX))
    except OSError:
        return []


def _row(replayed: dict[str, Any], cmp: dict[str, Any]) -> dict[str, Any]:
    item = replayed["item"]
    return {
        "item_id": cmp["item_id"], "title": one_line(item.get("title") or item.get("question") or "Activity"), "type": cmp["type"],
        "frozen": cmp["frozen"], "same_result": cmp["same_result"],
        "answers_before": cmp["answers_before"], "answers_after": cmp["answers"],
        "whole_before": cmp.get("whole_before"), "whole_after": cmp.get("whole_after"),
        "added": cmp.get("added", []), "removed": cmp.get("removed", []), "merged": cmp.get("merged", {}),
        "count_changes": len(cmp.get("count_changes", {})),
        "reposts_dropped": cmp["reposts_dropped"], "windows_cut": cmp["windows_cut"],
    }


def _rebuilt(folder: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The frozen captures recomputed with today's rules; a capture never frozen has no result to rebuild."""
    return [(r, c) for r, c in replay_records(folder, drop_reposts=True, plan_options=True) if c["frozen"]]


def _summary(folder: Path, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    rows = [_row(r, c) for r, c in pairs]
    changed = [r for r in rows if not r["same_result"] or r["answers_before"] != r["answers_after"]]
    kept = backups(folder)
    return {"activities": rows, "changed": len(changed), "reposts_dropped": sum(r["reposts_dropped"] for r in rows),
            "windows_cut": sum(r["windows_cut"] for r in rows), "backup": kept[-1] if kept else None}


def preview(folder: Path) -> dict[str, Any]:
    """What a rebuild would change, per activity; writes nothing."""
    return _summary(folder, _rebuilt(folder))


def _record(replayed: dict[str, Any]) -> str:
    return json.dumps({k: replayed[k] for k in RECORD_KEYS}, ensure_ascii=False, indent=1)


def _copy_backup(folder: Path, name: str) -> Path:
    live = _live(folder)
    dest = live / name
    shutil.copytree(live / "captures", dest / "captures")
    if (live / STATE_FILE).is_file():
        shutil.copy2(live / STATE_FILE, dest / STATE_FILE)
    return dest


def _restore(folder: Path, backup: Path) -> None:
    """``live/captures/`` and ``captures.json`` exactly as ``backup`` holds them."""
    live = _live(folder)
    kept = {p.name for p in (backup / "captures").iterdir()}
    for path in (live / "captures").iterdir():
        if path.is_file() and path.name not in kept:
            path.unlink()
    for src in (backup / "captures").iterdir():
        shutil.copy2(src, live / "captures" / src.name)
    if (backup / STATE_FILE).is_file():
        shutil.copy2(backup / STATE_FILE, live / STATE_FILE)


def _swap_in(folder: Path, staged: Path, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    live = _live(folder)
    state_path = live / STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else None
    for replayed, cmp in pairs:
        item_id = cmp["item_id"]
        atomic_write_text(live / "captures" / f"{item_id}.json", _record(replayed))
        png = staged / f"{item_id}.png"
        if png.is_file():
            tmp = live / "captures" / f"{item_id}.png.tmp"
            shutil.copy2(png, tmp)
            os.replace(tmp, live / "captures" / f"{item_id}.png")
        if state is not None and item_id in (state.get("windows") or {}):
            state["windows"][item_id] = replayed["windows"]
    if state is not None:
        atomic_write_text(state_path, json.dumps(state, indent=1))


def apply(folder: Path) -> dict[str, Any]:
    """Rebuild the session's captures: draw first, back up, swap in; returns the summary with the new backup's name."""
    if not _busy.acquire(blocking=False):
        raise RebuildError(409, "busy", "A rebuild is already running for this app")
    staged: Optional[Path] = None
    try:
        pairs = _rebuilt(folder)
        if not pairs:
            raise RebuildError(409, "nothing_to_rebuild", "No capture of this session was frozen yet")
        now = datetime.now(UTC)
        for replayed, _ in pairs:
            replayed["frozen_at"] = now.isoformat(timespec="seconds")
        scratch = data_dir() / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix="rebuild-", dir=scratch))
        for replayed, cmp in pairs:
            (staged / f"{cmp['item_id']}.json").write_text(_record(replayed), encoding="utf-8")
        failed = render_all(folder, staged, [c["item_id"] for _, c in pairs])
        if failed:
            raise RebuildError(502, "render_failed", f"{len(failed)} stage picture(s) could not be drawn, so nothing was changed — see the log",
                               detail=failed)
        backup = _copy_backup(folder, BACKUP_PREFIX + now.astimezone().strftime("%Y%m%d-%H%M%S-%f"))
        try:
            _swap_in(folder, staged, pairs)
        except (OSError, ValueError) as exc:
            _restore(folder, backup)
            raise RebuildError(500, "write_failed", f"Could not write the rebuilt results ({exc}); the previous ones were put back") from exc
        logger.info("✅ rebuilt %d captures of %s (backup %s)", len(pairs), folder.name, backup.name)
        return {**_summary(folder, pairs), "backup": backup.name}
    finally:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        _busy.release()


def undo(folder: Path) -> dict[str, Any]:
    """Put the latest backup back; the backup folder is kept, renamed so the next undo reaches the one before."""
    if not _busy.acquire(blocking=False):
        raise RebuildError(409, "busy", "A rebuild is already running for this app")
    try:
        kept = backups(folder)
        if not kept:
            raise RebuildError(409, "no_backup", "There is no rebuild to undo")
        backup = _live(folder) / kept[-1]
        _restore(folder, backup)
        backup.rename(_live(folder) / (UNDONE_PREFIX + kept[-1][len(BACKUP_PREFIX):]))
        logger.info("✅ restored the captures of %s from %s", folder.name, kept[-1])
        return {"restored": kept[-1], "backup": (backups(folder) or [None])[-1]}
    finally:
        _busy.release()
