"""What the *Analyze session* button copies, and what the analysis will find in the session folder.

The app starts no agent and makes no LLM call: the facilitator pastes the command into a coding agent
session started in the repo root, where the ``analyze-session`` skill reads the session through
``python -m src.results.dump``. This module only names the command and reports which inputs exist.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.results.dump import NOTES_FILE, REPORT_DIR
from src.sessions.playbook import PLAYBOOK_FILE

logger = logging.getLogger(__name__)

SKILL = "analyze-session"
HINT = "Paste it into an agent session started in the facilitation-suite repo root."


def command(folder: Path) -> str:
    """The skill invocation for ``folder``; the path is quoted because a session folder may hold spaces."""
    return f'/{SKILL} "{folder}"'


def _reports(folder: Path) -> list[str]:
    """The files already in ``report/``, oldest name first (they carry a date), none when the folder is absent."""
    try:
        return sorted(p.name for p in (folder / REPORT_DIR).iterdir() if p.is_file())
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("⚠️ analysis: %s unreadable (%s) — shown as empty", REPORT_DIR, exc)
        return []


def analysis_facts(folder: Path, captured: int) -> dict[str, Any]:
    """The button's state: the command, the hint, which inputs exist and the reports already written.

    ``captured`` is the number of activities with a capture; without one there is nothing to analyse and
    ``reason`` says so (the button is disabled with it).
    """
    return {
        "command": command(folder),
        "hint": HINT,
        "captured": captured,
        "reason": "" if captured else "Capture at least one activity first — there is nothing to analyse yet.",
        "playbook": (folder / PLAYBOOK_FILE).is_file(),
        "notes": (folder / NOTES_FILE).is_file(),
        "reports": _reports(folder),
    }
