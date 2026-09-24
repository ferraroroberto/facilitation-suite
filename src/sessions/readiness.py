"""The "ready for the live session" checklist (epic §3.1 step 5).

Each check reports one of four states — ``ok``, ``warn`` (needs attention),
``todo`` (not done yet) or ``unknown`` (the app could not establish it) —
and ``unknown`` is never counted as passing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.sessions.model import Session
from src.sessions.offline import OfflineReport


def _check(key: str, label: str, state: str, detail: str, action: Optional[str] = None) -> dict[str, Any]:
    out = {"key": key, "label": label, "state": state, "detail": detail}
    if action:
        out["action"] = action
    return out


def slides_meta(folder: Path) -> Optional[dict[str, Any]]:
    path = folder / "slides" / "slides.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build(folder: Path, session: Session, offline: OfflineReport, live: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    live = live or {}
    checks: list[dict[str, Any]] = []

    meta = slides_meta(folder)
    if meta and meta.get("slides"):
        src = Path(str(meta.get("source") or "")).name or "PowerPoint"
        checks.append(_check("slides", "Slides", "ok", f"{len(meta['slides'])} from {src}"))
    else:
        checks.append(_check("slides", "Slides", "todo", "No slides imported yet", "import"))

    acts = [it for it in session.all_items() if it.kind == "activity"]
    skipped = [a for a in acts if not a.include]
    unconfigured = [a for a in acts if a.include and a.type not in ("groups_reveal",) and not a.question.strip()]
    if not acts:
        checks.append(_check("activities", "Activities", "todo", "No activities in the plan yet"))
    elif unconfigured:
        checks.append(_check("activities", "Activities", "warn", f"{len(unconfigured)} without a question"))
    else:
        detail = f"{len(acts) - len(skipped)} configured" + (f" · {len(skipped)} skipped" if skipped else "")
        checks.append(_check("activities", "Activities", "ok", detail))

    roster = live.get("roster")
    if roster:
        checks.append(_check("roster", "Roster", "ok", f"{roster['present']} present of {roster['total']}"))
    elif (folder / "roster.xlsx").is_file():
        checks.append(_check("roster", "Roster", "ok", "roster.xlsx in the folder"))
    else:
        checks.append(_check("roster", "Roster", "todo", "No roster yet (Groups tab)"))

    if (folder / "groups.yaml").is_file():
        checks.append(_check("groups", "Groups", "ok", "Pairs + two rounds of 4 saved"))
    else:
        checks.append(_check("groups", "Groups", "todo", "Not shuffled yet (Groups tab)"))

    if offline.state == "ok":
        checks.append(_check("offline", "Files offline", "ok",
                             f"{offline.local} of {offline.total} files on this PC" + (" · always kept offline" if offline.pinned else "")))
    elif offline.state == "cloud_only":
        checks.append(_check("offline", "Files offline", "warn", offline.detail, "pin"))
    else:
        checks.append(_check("offline", "Files offline", "unknown", offline.detail or "Could not check"))

    reader = live.get("reader")
    if reader and reader.get("tested"):
        checks.append(_check("reader", "Zoom chat reader", "ok", reader["detail"]))
    else:
        checks.append(_check("reader", "Zoom chat reader", "unknown", "Not tested yet — pop out the chat and test", "test_reader"))

    obs = live.get("obs")
    if obs and obs.get("connected"):
        checks.append(_check("obs", "OBS", "ok", obs.get("detail", "Connected")))
    elif obs and obs.get("enabled") is False:
        checks.append(_check("obs", "OBS", "warn", "Scene switching is off in the config"))
    else:
        checks.append(_check("obs", "OBS", "unknown", (obs or {}).get("detail") or "Not connected"))

    if session.checklist.get("zoom_autoupdate_off"):
        checks.append(_check("zoom_update", "Zoom auto-update", "ok", "Turned off"))
    else:
        checks.append(_check("zoom_update", "Zoom auto-update", "warn", "Turn it off the week before", "confirm_zoom_update"))
    return checks
