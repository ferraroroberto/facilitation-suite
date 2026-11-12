"""Two conventions of the page scripts under ``app/webapp/static/js``, checked on the source so a new offender fails here:

* ``data-state`` has one writer, ``card-state.js`` (a card's loading / ready / empty / error); a flow with words of its own uses another attribute.
* a ``setTimeout`` / ``setInterval`` delay of 100 ms or more is a named constant, so its reason sits next to its value.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import PROJECT_ROOT

JS_DIR = PROJECT_ROOT / "app" / "webapp" / "static" / "js"
STATE_OWNER = "card-state.js"
MIN_NAMED_DELAY_MS = 100

STATE_WRITE = re.compile(r"""dataset\.state\s*=(?!=)|setAttribute\(\s*['"]data-state['"]|data-state\s*=""")
TIMER_CALL = re.compile(r"\b(?:setTimeout|setInterval)\(")
NUMBER = re.compile(r"\d[\d_]*")
QUOTES = "'\"`"
OPENERS = "([{"
CLOSERS = ")]}"


def _scripts() -> list[Path]:
    return sorted(JS_DIR.rglob("*.js"))


def _last_argument(source: str, start: int) -> str:
    """The text of the last top-level argument of the call whose ``(`` ends just before ``start``."""
    depth = 0
    quote = ""
    last = start
    i = start
    while i < len(source):
        c = source[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in QUOTES:
            quote = c
        elif c in OPENERS:
            depth += 1
        elif c in CLOSERS:
            if depth == 0:
                return source[last:i].strip()
            depth -= 1
        elif c == "," and depth == 0:
            last = i + 1
        i += 1
    return ""


def timer_literals(source: str) -> list[tuple[int, int]]:
    """``(line, delay)`` of every timer call whose delay is a bare number of ``MIN_NAMED_DELAY_MS`` or more."""
    found = []
    for call in TIMER_CALL.finditer(source):
        arg = _last_argument(source, call.end())
        if NUMBER.fullmatch(arg) and int(arg.replace("_", "")) >= MIN_NAMED_DELAY_MS:
            found.append((source.count("\n", 0, call.start()) + 1, int(arg.replace("_", ""))))
    return found


def test_data_state_has_one_writer() -> None:
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}:{n}"
        for path in _scripts()
        if path.name != STATE_OWNER
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if STATE_WRITE.search(line)
    ]
    assert not offenders, f"only {STATE_OWNER} writes data-state; give the flow its own attribute: {offenders}"


def test_timer_delays_are_named() -> None:
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}:{n} ({ms} ms)"
        for path in _scripts()
        for n, ms in timer_literals(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"name the delay as a constant with its reason: {offenders}"


def test_timer_scan_reads_a_multiline_callback() -> None:
    source = "a = setInterval(() => {\n  go('x, y');\n}, 250);\nb = setTimeout(go, LIMIT_MS);\nc = setTimeout(() => d(1, 2), 0);\n"
    assert timer_literals(source) == [(1, 250)]
