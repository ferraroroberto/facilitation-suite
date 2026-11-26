"""Structural guard: one definition per literal table. A module-level tuple, list, set or dict written
out with the same items in two modules is two copies of one rule (the NPS bands scored by a chat
activity and by the feedback forms) that drift when only one is edited, and it sits far below the
size of block the static ratchet's duplicate detector reports. Define it once and import it."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "app")
# Vendored components are copies by design.
EXCLUDE_PARTS = ("_vendored",)
WRAPPERS = ("frozenset", "set", "tuple")


def _table(node: ast.stmt) -> str:
    """The shape of a module-level literal collection of two or more items, else ``""``."""
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return ""
    value = node.value
    inner = value
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in WRAPPERS and len(value.args) == 1:
        inner = value.args[0]
    try:
        literal = ast.literal_eval(inner)
    except (ValueError, TypeError):
        return ""
    return ast.dump(value) if isinstance(literal, (tuple, list, set, dict)) and len(literal) >= 2 else ""


def duplicated_tables(sources: dict[str, str]) -> list[list[str]]:
    """For ``{path: python source}``: each table found in more than one file, as ``path:line`` places."""
    places: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path, text in sources.items():
        for node in ast.parse(text, filename=path).body:
            shape = _table(node)
            if shape:
                places[shape].append((path, node.lineno))
    return [[f"{p}:{line}" for p, line in found] for found in places.values() if len({p for p, _ in found}) > 1]


def test_the_guard_sees_a_table_written_in_two_modules() -> None:
    bands = 'BANDS = (("high", 9, 10), ("low", 0, 8))\n'
    assert duplicated_tables({"a.py": bands, "b.py": "OTHER" + bands}) == [["a.py:1", "b.py:1"]]
    assert duplicated_tables({"a.py": bands, "b.py": "from a import BANDS\n"}) == []


def test_no_literal_table_is_defined_in_two_modules() -> None:
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for d in SCAN_DIRS
        for path in sorted((ROOT / d).rglob("*.py"))
        if not any(part in EXCLUDE_PARTS for part in path.parts)
    }
    duplicated = duplicated_tables(sources)
    assert not duplicated, "The same literal table is defined in more than one module; define it once and import it:\n" + \
        "\n".join(" = ".join(places) for places in duplicated)
