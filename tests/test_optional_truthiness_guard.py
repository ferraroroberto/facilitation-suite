"""Structural guard (#298): an attribute typed ``Optional[int]``/``Optional[float]``
in ``__init__`` must never be truthiness-tested elsewhere in the same class — 0 is
a legitimate value and a bare ``if self.x:`` silently treats it the same as
"absent". That exact shape dropped a Spotify volume-to-0 change
(``src/music/spotify.py``, #298): the guard tested ``self.volume_now`` for
truthiness instead of ``is not None``, so a volume of 0 was read as "no track
loaded" and the next ``set_volume`` call became a no-op. Compare to
``is not None`` (or ``is None``) instead; a fallback default (``self.x or 0``)
is fine and is not flagged — only a guard that decides whether to act at all."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "app")
# Vendored components keep their upstream style byte-for-byte — never edited here.
EXCLUDE_PARTS = ("_vendored",)
OPTIONAL_NUMERIC_TYPES = {"int", "float"}


def _optional_numeric_attrs(cls: ast.ClassDef) -> set[str]:
    """Attribute names annotated ``self.x: Optional[int|float]`` anywhere in the class."""
    attrs: set[str] = set()
    for node in ast.walk(cls):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute)
                and isinstance(node.target.value, ast.Name) and node.target.value.id == "self"):
            continue
        ann = node.annotation
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Optional":
            inner = ann.slice
            if isinstance(inner, ast.Name) and inner.id in OPTIONAL_NUMERIC_TYPES:
                attrs.add(node.target.attr)
    return attrs


def _as_self_attr(expr: ast.expr, attrs: set[str]) -> str:
    """``self.<attr>`` when ``expr`` is exactly that (bare, no call/comparison) and tracked; else ``""``."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self" \
            and expr.attr in attrs:
        return expr.attr
    return ""


def _truthiness_violations(cls: ast.ClassDef, attrs: set[str]) -> list[tuple[int, str]]:
    """Every ``if``/``while``/ternary/``not`` whose whole condition is a bare tracked attribute."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(cls):
        test = getattr(node, "test", None) if isinstance(node, (ast.If, ast.While, ast.IfExp)) else None
        if test is None:
            continue
        target = test.operand if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) else test
        attr = _as_self_attr(target, attrs)
        if attr:
            found.append((node.lineno, attr))
    return found


def _offenders() -> list[str]:
    offenders = []
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                attrs = _optional_numeric_attrs(node)
                if not attrs:
                    continue
                for lineno, attr in _truthiness_violations(node, attrs):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} self.{attr}")
    return offenders


def test_no_optional_numeric_attribute_is_truthiness_tested() -> None:
    offenders = _offenders()
    assert not offenders, (
        "Optional[int]/Optional[float] attribute(s) guarded by bare truthiness instead of "
        "`is not None` — 0 silently reads as absent (#298):\n" + "\n".join(offenders)
    )
