"""Structural guard (audit #389): a module/class/function docstring in ``src/`` or ``app/``
must not carry more issue-number or build-step history (``#NNN``, "Step N") than the baseline
below. The vendored comment ratchet (``scripts/comment_ratchet.py``, fleet-config#1517) scans
only ``#`` *comments* -- a docstring is explicitly out of its scope -- so this whole class of
drift went ungated until now. A docstring states the present-tense why; how the code got there
lives in the issue, the PR and ``git log``.

Existing debt predates this guard and is baselined below at the count measured when it was
added, mirroring this repo's own ratchet convention (``docs/layout.md`` neighbours,
``.fleet.toml``'s ``[comment_ratchet]`` etc.): it never blocks unrelated work, but it cannot
grow, and a file cleaned of its history markers must have its entry lowered or removed here,
never raised."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "app")
EXCLUDE_PARTS = ("_vendored",)
# Mirrors the [vendored] table in .fleet.toml: copied byte-for-byte from project-scaffolding,
# which keeps its own comment style -- never reworded in this repo.
VENDORED_FILES = {
    "src/build_info.py",
    "src/no_window.py",
    "src/usage_log.py",
    "app/tray/single_instance.py",
    "app/tray/watchdog.py",
}
_REF = re.compile(r"(?<![&#])#(\d{1,5})\b")

# path -> count at the time this guard was added (fleet-config#1520's structural-check
# requirement for a *recurring* audit bucket). Shrink an entry as a file is cleaned up;
# never raise one -- add a genuinely new exception by fixing the docstring instead.
BASELINE: dict[str, int] = {
    "app/player/__init__.py": 1,
    "app/player/app.py": 4,
    "app/player/listener.py": 1,
    "app/player/push.py": 1,
    "app/tray/tray.py": 1,
    "app/webapp/assets.py": 1,
    "app/webapp/event_loop.py": 2,
    "app/webapp/routers/actions.py": 1,
    "app/webapp/routers/chat.py": 1,
    "app/webapp/routers/quiz.py": 2,
    "app/webapp/routers/settings.py": 3,
    "app/webapp/server.py": 3,
    "src/chat/process.py": 1,
    "src/chat/reader.py": 2,
    "src/chat/teams.py": 1,
    "src/chat/uia.py": 1,
    "src/config.py": 5,
    "src/defaults.py": 1,
    "src/env_file.py": 2,
    "src/library.py": 1,
    "src/live/hub.py": 1,
    "src/music/service.py": 6,
    "src/quiz/__init__.py": 1,
    "src/quiz/chat.py": 1,
    "src/quiz/cues.py": 3,
    "src/quiz/importer.py": 1,
    "src/quiz/model.py": 2,
    "src/quiz/qr.py": 1,
    "src/quiz/reach.py": 3,
    "src/quiz/results.py": 1,
    "src/static_versioning.py": 1,
    "src/tunnel.py": 1,
}


def _docstring_history_counts() -> dict[str, int]:
    """``{repo-relative path: #NNN references across every docstring in the file}``."""
    counts: dict[str, int] = {}
    for base in SCAN_DIRS:
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if any(part in EXCLUDE_PARTS for part in path.parts) or rel in VENDORED_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            nodes: list[ast.AST] = [tree] + [
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            count = 0
            for node in nodes:
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    count += len(_REF.findall(doc))
            if count:
                counts[rel] = count
    return counts


def test_no_docstring_gains_more_issue_or_step_history_than_the_baseline() -> None:
    current = _docstring_history_counts()
    offenders = [
        f"{path}: {count} (baseline {BASELINE.get(path, 0)})"
        for path, count in sorted(current.items())
        if count > BASELINE.get(path, 0)
    ]
    assert not offenders, (
        "docstring(s) carry more #NNN issue/build-step history than the baseline allows -- "
        "reword to the present-tense why and leave the history to the issue, the PR and "
        "git log:\n" + "\n".join(offenders)
    )
