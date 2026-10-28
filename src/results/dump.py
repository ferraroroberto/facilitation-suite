"""A session's results as one JSON document, for the analysis skill (``.claude/skills/analyze-session``).

``python -m src.results.dump <session folder>`` prints it; ``--last`` picks the ledger's session with the
latest date instead of a folder. It reads the same helpers the Results tab, the PDF and the Excel report
read, so the people, their merged names, the senders left out and the order the activities ran in are the
ones those show: the dump is never a second interpretation of ``live/``. It writes nothing.

The facilitator's ``playbook.md`` and ``notes.md`` ride along as text (``null`` when absent): the skill
reads the session's instructions and the facilitator's own observations from the same document.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from src.config import load_config
from src.errors import DomainError
from src.groups.roster import roster_state
from src.jsonl import read_jsonl
from src.logger import configure_logging
from src.results.aliases import Excluded, read_aliases, seen_names, suggest
from src.results.collect import load_results, participation, participation_table
from src.sessions.playbook import PLAYBOOK_FILE
from src.sessions.store import SessionStore, read_session_file

logger = logging.getLogger(__name__)

NOTES_FILE = "notes.md"
# The analysis report's folder: the skill writes there and nothing else in the app does.
REPORT_DIR = "report"
USAGE = "usage: python -m src.results.dump <session folder> | --last"


def _text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("⚠️ dump: %s unreadable (%s) — left out", path.name, exc)
        return None


def _roster(folder: Path) -> list[dict[str, Any]]:
    try:
        people = roster_state(folder)[0]
    except DomainError as exc:
        logger.warning("⚠️ dump: roster unreadable (%s) — left out", exc)
        return []
    return [{"name": p.name, "role": p.role, "present": p.present} for p in people]


def _activity(a: dict[str, Any]) -> dict[str, Any]:
    """One activity with its counted answers only: a hidden or left-out answer is not evidence about anyone."""
    return {
        "number": a["number"], "id": a["id"], "title": a["title"], "question": a["question"],
        "type": a["type"], "type_label": a["type_label"], "captured": a["captured"],
        "answers": a["answers"], "people": a["people"], "hidden": a["hidden"],
        "answer_rows": [{"sender": r["sender"], "as": r["raw"], "time": r["time"], "text": r["text"], "value": r["value"]}
                        for r in a["answer_rows"] if not r["hidden"]],
    }


def dump(folder: Path) -> dict[str, Any]:
    """The analysis document for ``folder``; raises ``SessionError`` when it holds no readable ``session.yaml``."""
    session = read_session_file(folder)
    data = load_results(folder, session)
    aliases = read_aliases(folder)
    excluded = Excluded(session.excluded_senders, aliases)
    chat = read_jsonl(folder / "live" / "chat.jsonl")
    people = participation(data["activities"], chat, aliases, excluded)
    roster = _roster(folder)
    return {
        "folder": str(folder),
        "report_dir": str(folder / REPORT_DIR),
        "session": data["session"],
        "went_live": data["went_live"],
        "excluded_senders": list(session.excluded_senders),
        "merged_names": aliases.groups,
        # The Results tab's own likely merges, so the skill proposes the same ones the app would.
        "merge_suggestions": suggest(seen_names(data["activities"], chat, excluded), aliases, [p["name"] for p in roster]),
        "activities": [_activity(a) for a in data["activities"]],
        "participation": participation_table(data["activities"], people),
        "roster": roster,
        "playbook": _text(folder / PLAYBOOK_FILE),
        "notes": _text(folder / NOTES_FILE),
    }


def last_session_folder() -> Path:
    """The ledger's session with the latest date (a session without a date, or unreadable, never wins)."""
    best: Optional[tuple[float, Path]] = None
    for entry in SessionStore(load_config()).entries():
        folder = Path(entry.path)
        try:
            session = read_session_file(folder)
        except DomainError as exc:
            logger.warning("⚠️ dump: %s skipped (%s)", folder.name, exc)
            continue
        if session.date is None:
            continue
        when = session.date.timestamp()  # naive and zoned dates compare as instants
        if best is None or when > best[0]:
            best = (when, folder)
    if best is None:
        raise LookupError("no session with a date in the ledger")
    return best[1]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 1
    configure_logging(to_file=False)  # stdout carries only the JSON; logs go to stderr
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        folder = last_session_folder() if argv[1] == "--last" else Path(argv[1])
        doc = dump(folder)
    except (DomainError, LookupError) as exc:
        logger.error("❌ dump %s: %s", argv[1], exc)
        return 2
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
