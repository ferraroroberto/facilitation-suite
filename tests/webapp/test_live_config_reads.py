"""A Settings save replaces ``app.state.config``, so a callback that outlives its installer must read it
through ``app.state`` at call time. A closure over a local captured from ``app.state.config`` goes stale."""

from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "app" / "webapp" / "server.py"


def _is_state_config(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "config" and isinstance(node.value, ast.Attribute) and node.value.attr == "state"


def _stale_reads(fn: ast.FunctionDef) -> list[str]:
    captured = {
        t.id
        for stmt in fn.body
        if isinstance(stmt, ast.Assign) and _is_state_config(stmt.value)
        for t in stmt.targets
        if isinstance(t, ast.Name)
    }
    found: list[str] = []
    for inner in ast.walk(fn):
        if inner is fn or not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for name in ast.walk(inner):
            if isinstance(name, ast.Name) and name.id in captured and isinstance(name.ctx, ast.Load):
                found.append(f"{fn.name}: {name.id} (line {name.lineno})")
    return found


def test_no_server_callback_reads_the_config_captured_at_startup() -> None:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    stale = [hit for fn in tree.body if isinstance(fn, ast.FunctionDef) for hit in _stale_reads(fn)]
    assert stale == [], f"read app.state.config at call time, not the startup capture: {stale}"
