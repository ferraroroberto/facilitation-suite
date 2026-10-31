"""A bare function handed to an empty state's ``onAction`` must take no arguments.

The vendored empty state attaches it with ``addEventListener('click', onAction)``, so the
click Event arrives as the first argument; a handler that expects a context there throws
before it does anything. ``onAction: reimport`` did exactly that on the empty plan. A handler
that needs arguments is wrapped in an arrow function instead.
"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[2] / "app" / "webapp" / "static" / "js"

BARE_HANDLER = re.compile(r"\bonAction:\s*([A-Za-z_$][\w$]*)\s*[,}]")


def _takes_no_arguments(source: str, name: str) -> bool:
    """True when ``name`` is declared in ``source`` as a function or arrow function with an empty parameter list."""
    declared = re.search(rf"(?:function\s+{re.escape(name)}\s*\(([^)]*)\)|(?:const|let)\s+{re.escape(name)}\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>)", source)
    return declared is not None and not (declared.group(1) or declared.group(2) or "").strip()


def test_a_bare_onaction_handler_is_declared_in_its_file_without_parameters() -> None:
    offenders = []
    for path in sorted(JS_DIR.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        for name in BARE_HANDLER.findall(source):
            if not _takes_no_arguments(source, name):
                offenders.append(f"{path.relative_to(JS_DIR).as_posix()}: onAction: {name}")
    assert not offenders, "wrap in () => ... (the click Event would be its first argument): " + "; ".join(offenders)
