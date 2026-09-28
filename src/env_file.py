"""The ``.env`` secrets file: read it, and change one key in place.

Plain ``KEY=value`` lines (``#`` comments, blank lines and optional quotes);
no dependency. A key set in the process environment wins over the file. The
file is gitignored and never logged; ``set_value`` keeps every other line as
it was and writes atomically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.config import env_path


def _parse(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.removeprefix("export ").partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return key.strip(), value


def read_env(path: Optional[Path] = None) -> dict[str, str]:
    path = path or env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    return dict(kv for kv in map(_parse, lines) if kv)


def get_value(key: str, path: Optional[Path] = None) -> str:
    """The process environment first, then ``.env``; ``""`` when neither has it."""
    return (os.environ.get(key) or read_env(path).get(key) or "").strip()


def set_value(key: str, value: str, path: Optional[Path] = None) -> Path:
    """Set ``key`` in ``.env`` (replacing its line, or appending one)."""
    path = path or env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for line in lines:
        kv = _parse(line)
        if kv and kv[0] == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
