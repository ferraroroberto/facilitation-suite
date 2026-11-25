"""The Room Polls demo session: a Join slide, a quiz, every poll type and a private form.

The plan itself is ``tests/fixtures/demo-room-polls/session.yaml`` (synthetic,
activities only, so no deck is needed). Tests write it into a disposable
session folder with :func:`build_room_polls_demo`; the owner creates it on a
running app with ``python -m scripts.room_polls_demo`` (``docs/room-polls.md``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from src.sessions.model import dump_session, parse_session
from src.sessions.store import SESSION_FILE, SUBDIRS, session_id

DEMO_FILE = Path(__file__).resolve().parent / "demo-room-polls" / SESSION_FILE

# The poll types the demo must carry, each with the phone control it is answered with.
POLL_TYPES = ("word_cloud", "choice", "scale", "stars", "nps", "number", "feed")


def load_plan() -> dict[str, Any]:
    """The demo plan as ``session.yaml`` holds it."""
    return yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))


def demo_items(plan: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Every item of the plan, in order."""
    plan = plan or load_plan()
    return [it for sec in plan["sections"] for it in sec["items"]]


def build_room_polls_demo(folder: Path, ledger: Optional[Path] = None) -> tuple[str, Path]:
    """Write the demo session into ``folder`` (and append it to ``ledger``, when given)."""
    for sub in SUBDIRS:
        (folder / sub).mkdir(parents=True, exist_ok=True)
    session = parse_session(load_plan())
    (folder / SESSION_FILE).write_text(yaml.safe_dump(dump_session(session), sort_keys=False, allow_unicode=True),
                                       encoding="utf-8")
    if ledger is not None:
        entries = []
        if ledger.is_file():
            entries = (yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}).get("sessions") or []
        entries.append({"name": session.title, "path": str(folder)})
        ledger.write_text(yaml.safe_dump({"sessions": entries}, allow_unicode=True), encoding="utf-8")
    return session_id(folder), folder
