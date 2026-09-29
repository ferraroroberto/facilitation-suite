"""The ``.env`` secrets file: read it, and change one key in place.

Plain ``KEY=value`` lines (``#`` comments, blank lines and optional quotes);
no dependency. A key set in the process environment wins over the file. The
file is gitignored and never logged; ``set_value`` keeps every other line as
it was and writes atomically. A read is cached for a fraction of a second
(#141) — a save made through this module invalidates it at once, but an edit
made outside the app (by hand, or another process) can take that long to show.
``generation()`` counts this process's saves, so a cache built on ``.env``
elsewhere can tell whether a save landed since it was computed (#146).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from src.config import env_path
from src.sessions.store import replace_held

# The mirror image of ``replace_held``'s write retry: a read can land in the instant a save's
# ``os.replace`` is itself in flight (Windows briefly refuses either side of that race, not just
# the writer, #141). A read runs on every Spotify status poll, so this stays short.
READ_RETRY_WAITS = (0.005, 0.01, 0.02, 0.05, 0.1)

# ``.env`` is read far more often than it is written (every status poll opens it, often more than
# once in the same request) — caching a parse for a short window cuts how many real file opens
# happen, which is what actually keeps a save's ``os.replace`` from colliding with a reader in the
# first place (#141), rather than just giving the writer a longer, riskier wait to outlast one. A
# save updates the cache with the exact bytes it just wrote, so a read right after is never stale.
_CACHE_TTL_S = 0.2
_cache_lock = threading.Lock()
_cache: dict[Path, tuple[float, dict[str, str]]] = {}
# Bumped by every save (#146). Anything computed from ``.env`` — this module's read cache, the
# Spotify status and access token — remembers the generation it started from and is not served
# once a save has landed since: a read or a status check still in flight when a login is saved
# otherwise stores its pre-save answer over the fresh one.
_generation = 0


def _read_text(path: Path) -> str:
    for wait in READ_RETRY_WAITS:
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(wait)
    return path.read_text(encoding="utf-8")  # the last try raises what Windows says


def _parse(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.removeprefix("export ").partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return key.strip(), value


def _parse_all(text: str) -> dict[str, str]:
    return dict(kv for kv in map(_parse, text.splitlines()) if kv)


def generation() -> int:
    """How many saves this process has made to ``.env``: an answer computed from ``.env`` under an
    older generation predates a save and must not be served as current."""
    with _cache_lock:
        return _generation


def read_env(path: Optional[Path] = None) -> dict[str, str]:
    path = path or env_path()
    with _cache_lock:
        hit = _cache.get(path)
        if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_S:
            return hit[1]
        started = _generation
    try:
        parsed = _parse_all(_read_text(path))
    except FileNotFoundError:
        parsed = {}
    with _cache_lock:
        if _generation == started:  # a save landed mid-read: these bytes may predate it, never cache them
            _cache[path] = (time.monotonic(), parsed)
    return parsed


def get_value(key: str, path: Optional[Path] = None) -> str:
    """The process environment first, then ``.env``; ``""`` when neither has it."""
    return (os.environ.get(key) or read_env(path).get(key) or "").strip()


def set_value(key: str, value: str, path: Optional[Path] = None) -> Path:
    """Set ``key`` in ``.env`` (replacing its line, or appending one)."""
    global _generation
    path = path or env_path()
    try:
        lines = _read_text(path).splitlines()
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
    text = "\n".join(out) + "\n"
    tmp.write_text(text, encoding="utf-8")
    replace_held(tmp, path)  # a reader (the app's own status check) may hold it open for a moment
    with _cache_lock:  # a read right after this save must see it, not the old cache
        _generation += 1
        _cache[path] = (time.monotonic(), _parse_all(text))
    return path
