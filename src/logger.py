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

**An outside holder (#100).** One writer per file stops the *processes this
app owns* from fighting each other, but an outside process — a stray old
server from a previous session, an editor, a ``tail`` — can still hold
``facilitation-suite.log`` open, and Windows then refuses the rename rotation
needs (the same WinError 32) no matter how disciplined this app is.
``ResilientRotatingFileHandler`` keeps appending to the current file when that
happens instead of dropping records; see its docstring.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import data_dir

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_FLAG = "_fs_logging_configured"
_LEVELS = logging.getLevelNamesMapping()


class ResilientRotatingFileHandler(RotatingFileHandler):
    """A ``RotatingFileHandler`` that keeps writing when an outside process
    holds the file open (#100).

    On Windows, renaming a file another process has open fails with
    ``PermissionError`` (WinError 32) — proven with a plain read-only
    ``open()`` in a second process, no special share mode needed. Stock
    ``RotatingFileHandler.doRollover()`` closes its own stream, hits that
    rename and raises, leaving the stream closed; the next ``emit()`` then
    fails too and logging's default error handler (a one-line "Logging
    error" to stderr) silently drops the record.

    This subclass renames the *live* file first, before touching any backup
    generations — so a blocked rotation, retried indefinitely, never
    shifts ``.1``/``.2``/``.3`` out from under itself while nothing behind
    the live file has actually moved yet. When that first rename fails with
    ``PermissionError`` it:

    - reopens the current file in append mode so every later record still
      lands somewhere,
    - logs exactly one ⚠️ for the episode (not one per blocked ``emit()``),
    - stops attempting rotation until ``retry_after_records`` more records
      have been emitted *or* ``retry_after_seconds`` have passed, whichever
      comes first — frequent enough that a busy logger reclaims rotation
      promptly once the outside handle closes, bounded so a quiet one still
      retries without needing more traffic.

    Rotation resumes automatically the next time the outside handle is
    closed and a retry is due.
    """

    def __init__(
        self,
        *args: object,
        retry_after_records: int = 200,
        retry_after_seconds: float = 30.0,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.retry_after_records = retry_after_records
        self.retry_after_seconds = retry_after_seconds
        self._rotation_blocked = False
        self._records_since_block = 0
        self._blocked_at = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802 — stdlib override
        if not super().shouldRollover(record):
            return False
        if not self._rotation_blocked:
            return True
        self._records_since_block += 1
        elapsed = time.monotonic() - self._blocked_at
        return self._records_since_block >= self.retry_after_records or elapsed >= self.retry_after_seconds

    def doRollover(self) -> None:  # noqa: N802 — stdlib override
        if self.stream:
            self.stream.close()
            self.stream = None
        staged = self.baseFilename + ".rotating-tmp"
        try:
            os.replace(self.baseFilename, staged)
        except PermissionError as exc:
            if not self._rotation_blocked:
                logging.getLogger(__name__).warning(
                    "⚠️ log rotation blocked: %s is held open by another process (%s) — "
                    "still logging to it, will retry", self.baseFilename, exc,
                )
            self._rotation_blocked = True
            self._blocked_at = time.monotonic()
            self._records_since_block = 0
            self.stream = self._open()  # keep appending to the still-open live file
            return

        if self.backupCount > 0:
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
                if os.path.exists(sfn):
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    os.rename(sfn, dfn)
            dfn = self.rotation_filename(self.baseFilename + ".1")
            if os.path.exists(dfn):
                os.remove(dfn)
            self.rotate(staged, dfn)
        else:
            os.remove(staged)

        if self._rotation_blocked:
            logging.getLogger(__name__).info("✅ log rotation resumed: %s", self.baseFilename)
        self._rotation_blocked = False
        self._records_since_block = 0
        if not self.delay:
            self.stream = self._open()


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
        file_handler = ResilientRotatingFileHandler(
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
