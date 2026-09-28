"""One logger setup for every entrypoint (webapp, tray, scripts, CLI).

``configure_logging()`` is idempotent: the first caller installs a stream
handler (when a console exists) plus a rotating file under the data dir's
``logs/`` (``FS_DATA_DIR`` when set, so a test run or a scratch server never
writes into the tray's log — #26); later callers are no-ops. Under ``pythonw``
(the tray) ``sys.stderr`` is ``None`` — the file handler is then the *only*
durable trail, which is why it is always installed. Emoji markers are the
fleet convention: ℹ️ ⚠️ ❌ ✅.

**One writer per file (#37).** On Windows a file another process holds open
cannot be renamed, so two processes rotating one file both fail (WinError 32)
and drop every line once it is full. Each long-lived process therefore owns
its own file — the tray ``tray.log``, the webapp ``facilitation-suite.log``,
the chat reader ``chat-reader.log`` — and the short-lived helpers the webapp
spawns (freeze PNG, session PDF) log to stderr only (``to_file=False``); the
webapp reads their stderr and ``relay()``s it into its own log.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import data_dir

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_FLAG = "_fs_logging_configured"
_LEVELS = logging.getLevelNamesMapping()


def log_path(name: str) -> Path:
    """``<data dir>/logs/<name>.log`` — one file per process that writes one."""
    return data_dir() / "logs" / f"{name}.log"


def default_log_file() -> Path:
    """The webapp's log: ``<data dir>/logs/facilitation-suite.log``."""
    return log_path("facilitation-suite")


def configure_logging(level: int = logging.INFO, log_file: Path | None = None, *, to_file: bool = True) -> None:
    """Install the console + rotating-file handlers once per process.

    ``to_file=False`` is for helper processes whose parent captures stderr: they
    must never open (and so block the rotation of) a file another process owns.
    """
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return
    setattr(root, _CONFIGURED_FLAG, True)
    root.setLevel(level)
    formatter = logging.Formatter(_FMT, _DATEFMT)

    if sys.stderr is not None:
        try:
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if not to_file:
        return
    target = log_file or default_log_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # a read-only checkout must not kill startup
        root.warning("⚠️ logging: file handler unavailable (%s)", exc)


def relay(lines: Iterable[str], log: logging.Logger) -> None:
    """Re-log a ``to_file=False`` helper's stderr lines into this process's log.

    A line in the helper's own format (``<date> <time> <LEVEL> <name>: <message>``)
    keeps its level, so a helper's ❌ stays an ERROR here; anything else (a
    traceback, a library's own output) is relayed at INFO as-is.
    """
    for raw in lines:
        line = raw.rstrip()
        if not line:
            continue
        parts = line.split(None, 3)
        level = _LEVELS.get(parts[2]) if len(parts) == 4 else None
        if level is None:
            log.info("%s", line)
        else:
            log.log(level, "%s", parts[3])


def get_logger(name: str) -> logging.Logger:
    """``logging.getLogger`` after making sure the process is configured."""
    configure_logging()
    return logging.getLogger(name)
