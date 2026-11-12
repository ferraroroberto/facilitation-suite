"""Structural guard: prose in ``src/``, ``app/`` and ``assets/`` must not point into the build
history. A docstring or comment states the present-tense why; how the code got here lives in the
issue, the PR and ``git log``.

The vendored comment ratchet (``scripts/comment_ratchet.py``) counts only ``#NNN`` references
inside comments and is never edited here, so this test covers what it cannot:

* ``#NNN`` issue references inside Python docstrings (a docstring is not a comment token);
* ``epic §N`` pointers into the closed planning issue, in Python docstrings and comments and in
  JS / CSS files;
* ``step N`` build-step pointers (``N`` >= 1) in Python docstrings and comments -- a quiz podium's
  "step 0" is a game state, not a pointer, and is not matched.

No debt is baselined: a hit fails, and the fix is to reword it to what is true now."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "app", "assets")
EXCLUDE_PARTS = ("_vendored", "fleet")
# Mirrors the [vendored] table in .fleet.toml: copied byte-for-byte from project-scaffolding,
# which keeps its own comment style -- never reworded in this repo.
VENDORED_FILES = {
    "src/build_info.py",
    "src/no_window.py",
    "src/usage_log.py",
    "app/tray/single_instance.py",
    "app/tray/watchdog.py",
}
_ISSUE_REF = re.compile(r"(?<![&#])#(\d{1,5})\b")
_EPIC_POINTER = re.compile(r"\bepic\s*§", re.IGNORECASE)
_STEP_POINTER = re.compile(r"\bstep\s+[1-9]\d*\b", re.IGNORECASE)


def _python_prose(text: str, path: str) -> tuple[list[str], list[str]]:
    """``(docstrings, comments)`` of one Python source."""
    tree = ast.parse(text, filename=path)
    nodes: list[ast.AST] = [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    docstrings = [d for d in (ast.get_docstring(n, clean=False) for n in nodes) if d]
    comments = [
        tok.string for tok in tokenize.generate_tokens(io.StringIO(text).readline)
        if tok.type == tokenize.COMMENT
    ]
    return docstrings, comments


def _hits(path: Path) -> list[str]:
    """The rule names this file breaks, one entry per hit."""
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    if path.suffix == ".py":
        docstrings, comments = _python_prose(text, str(path))
        prose = docstrings + comments
        hits += ["issue-ref in docstring"] * sum(len(_ISSUE_REF.findall(d)) for d in docstrings)
        hits += ["step pointer"] * sum(len(_STEP_POINTER.findall(p)) for p in prose)
        hits += ["epic pointer"] * sum(len(_EPIC_POINTER.findall(p)) for p in prose)
    else:
        hits += ["epic pointer"] * len(_EPIC_POINTER.findall(text))
    return hits


def _offenders() -> list[str]:
    found: list[str] = []
    for base in SCAN_DIRS:
        for path in sorted((ROOT / base).rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if (path.suffix not in {".py", ".js", ".css"} or rel in VENDORED_FILES
                    or any(part in EXCLUDE_PARTS for part in path.parts)):
                continue
            found += [f"{rel}: {rule}" for rule in _hits(path)]
    return found


def test_scan_roots_exist() -> None:
    """A wrong ``ROOT`` once made this guard scan nothing and pass vacuously."""
    for base in SCAN_DIRS:
        assert (ROOT / base).is_dir(), base


def test_prose_carries_no_build_history_pointers() -> None:
    offenders = _offenders()
    assert not offenders, (
        "docstring(s) or comment(s) point into the build history (#NNN in a docstring, "
        "`epic §N`, `step N`) -- reword to the present-tense why and leave the history to "
        "the issue, the PR and git log:\n" + "\n".join(offenders)
    )
