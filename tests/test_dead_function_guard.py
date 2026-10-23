"""Structural guard: a top-level function in ``src/`` or ``app/`` must be named somewhere
besides its own ``def`` line. A function nothing refers to is dead code that outlives its
purpose (``logger.get_logger`` sat unused beside the ``logging.getLogger`` every module
actually calls), and ruff's F401/F841 only see unused imports and variables, not functions.

Decorated functions are exempt (a route, fixture or hook is reached by registration, not by
name), and the reference search is deliberately loose: any whole-word occurrence in any
tracked text file, docstring and comment included, counts, so it never flags a function that
is only reached through a string, a template or a config key. Delete the function, or
reference it from where it is really used."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "app")
CORPUS_SUFFIXES = {".py", ".js", ".html", ".md", ".toml", ".ps1", ".json", ".yaml", ".yml", ".css", ".bat"}
CORPUS_ROOTS = ("src", "app", "tests", "scripts", "docs", "themes", "config", ".github", ".claude")
SKIP_PARTS = {"_vendored", "__pycache__", "node_modules", ".venv"}


def _text_files() -> list[Path]:
    paths = [p for p in ROOT.iterdir() if p.is_file() and p.suffix in CORPUS_SUFFIXES]
    for name in CORPUS_ROOTS:
        base = ROOT / name
        if base.is_dir():
            paths += [p for p in base.rglob("*") if p.is_file() and p.suffix in CORPUS_SUFFIXES]
    # This file's docstring names the dead function it was written for, so it must not count as a reference.
    return [p for p in paths if p != Path(__file__).resolve() and not SKIP_PARTS & set(p.relative_to(ROOT).parts)]


def _top_level_functions() -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for name in SCAN_DIRS:
        for path in sorted((ROOT / name).rglob("*.py")):
            if SKIP_PARTS & set(path.relative_to(ROOT).parts):
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not node.decorator_list and not node.name.startswith("__")):
                    found.append((path, node.lineno, node.name))
    return found


def _dead_functions() -> list[str]:
    corpus = [p.read_text(encoding="utf-8", errors="replace") for p in _text_files()]
    dead: list[str] = []
    for path, lineno, name in _top_level_functions():
        word = re.compile(rf"\b{re.escape(name)}\b")
        if sum(len(word.findall(text)) for text in corpus) <= 1:
            dead.append(f"{path.relative_to(ROOT).as_posix()}:{lineno} {name}")
    return dead


def test_every_top_level_function_is_referenced():
    assert not _dead_functions(), "unreferenced top-level function(s) (delete them): " + ", ".join(_dead_functions())
