"""The "ready for the live session" checklist (epic §3.1 step 5).

Each check reports one of four states — ``ok``, ``warn`` (needs attention),
``todo`` (not done yet) or ``unknown`` (the app could not establish it) —
and ``unknown`` is never counted as passing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.importer.review import read_meta
from src.sessions.model import Session
from src.sessions.offline import OfflineReport


def _check(key: str, label: str, state: str, detail: str, action: Optional[str] = None) -> dict[str, Any]:
    out = {"key": key, "label": label, "state": state, "detail": detail}
    if action:
        out["action"] = action
    return out


def has_quiz(session: Session) -> bool:
    """The plan plays a quiz (an included quiz lobby, question or podium)."""
    from src.quiz.service import QUIZ_TYPES

    return any(it.kind == "activity" and it.include and it.type in QUIZ_TYPES for it in session.all_items())


# The public-link check's six states (src/quiz/reach.py) as checklist states: each keeps its own
# name in ``reach`` and in the detail, and ``unknown`` stays ``unknown`` — never counted as ok.
REACH_ROW = {"ok": "ok", "listener_down": "warn", "tunnel_down": "warn", "public_unreachable": "warn",
             "not_configured": "todo", "unknown": "unknown"}


def quiz_reach_check(reach: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The "Quiz public URL reachable" row from the last check (``None``: the app has no checker)."""
    reach = reach or {"state": "unknown", "label": "unknown", "detail": "Not checked yet"}
    state = reach["state"] if reach.get("state") in REACH_ROW else "unknown"
    detail = reach["detail"] if state == "ok" else f"{str(reach.get('label') or state).capitalize()} — {reach['detail']}"
    row = _check("quiz_reach", "Quiz public URL reachable", REACH_ROW[state], detail, "check_quiz_reach")
    row["reach"] = state
    return row


def build(folder: Path, session: Session, offline: OfflineReport, live: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    live = live or {}
    checks: list[dict[str, Any]] = []

    meta = read_meta(folder / "slides" / "slides.json")
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

    if session.font and session.font.file.strip():
        from src.sessions.theme import font_file

        name = Path(session.font.file).name
        if font_file(session) is not None:
            checks.append(_check("font", "Stage font", "ok", name))
        else:
            checks.append(_check("font", "Stage font", "warn", f"{name} is not on this PC — the stage falls back to Patrick Hand"))

    from src.groups.roster import RosterError, roster_state

    try:
        people, gstate = roster_state(folder)
    except RosterError as exc:
        people, gstate = [], {"rounds": None}
        checks.append(_check("roster", "Roster", "warn", str(exc)))
    if people:
        present = sum(1 for p in people if p.present)
        checks.append(_check("roster", "Roster", "ok", f"{present} present of {len(people)}"))
    elif not any(c["key"] == "roster" for c in checks):
        checks.append(_check("roster", "Roster", "todo", "No roster yet (Groups tab)"))

    rounds = gstate.get("rounds")
    if rounds and people:
        now = {p.name for p in people if p.present}
        shuffled = {n for g in rounds["pairs"] for n in g}
        if now != shuffled:
            checks.append(_check("groups", "Groups", "warn", "Presence changed since the shuffle — shuffle again"))
        else:
            checks.append(_check("groups", "Groups", "ok", f"{len(rounds['pairs'])} pairs + two rounds of 4 saved"))
    else:
        checks.append(_check("groups", "Groups", "todo", "Not shuffled yet (Groups tab)"))

    if offline.state == "ok":
        checks.append(_check("offline", "Files offline", "ok",
                             f"{offline.local} of {offline.total} files on this PC" + (" · always kept offline" if offline.pinned else "")))
    elif offline.state == "cloud_only":
        checks.append(_check("offline", "Files offline", "warn", offline.detail, "pin"))
    else:
        checks.append(_check("offline", "Files offline", "unknown", offline.detail or "Could not check"))

    from src.music.library import readiness as music_files
    from src.music.library import spotify_check, uses_spotify

    music = music_files(folder, session, offline)
    if music:
        checks.append(music)
    if uses_spotify(session):
        checks.append(spotify_check(live.get("spotify")))

    if has_quiz(session):
        checks.append(quiz_reach_check(live.get("quiz_reach")))

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
