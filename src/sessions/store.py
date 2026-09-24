"""Session folders and the ledger that lists them (epic §5).

- A **session** is a folder holding ``session.yaml`` plus ``slides/``,
  ``live/`` and ``exports/``. It lives outside the repo (default under
  ``config.session_root``, i.e. OneDrive) and is the only copy of its data.
- The **ledger** (``sessions.local.yaml``, gitignored) only lists names and
  folder paths so the app can show them. Removing a session from the ledger
  never deletes its folder.
- A session's **id** is a short hash of its folder path, so URLs and logs
  never carry a personal path.

Writes are atomic (temp file + replace) because the folder is synced by
OneDrive while the app runs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from src.config import AppConfig, ledger_path
from src.sessions.model import Session, dump_session, parse_session

logger = logging.getLogger(__name__)

SESSION_FILE = "session.yaml"
SUBDIRS = ("slides", "live", "exports")
# What a duplicate carries over: the plan and its inputs, never live data or exports.
DUPLICATE_ENTRIES = ("session.yaml", "slides", "roster.xlsx", "groups.yaml", "theme.css", "source")


class SessionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class LedgerEntry:
    name: str
    path: str

    @property
    def id(self) -> str:
        return session_id(Path(self.path))


def session_id(path: Path) -> str:
    norm = os.path.normcase(os.path.abspath(str(path)))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _yaml_dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _slug_folder(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else " " for c in text).strip()
    return "-".join(keep.split()) or "session"


class SessionStore:
    def __init__(self, config: AppConfig, ledger: Optional[Path] = None) -> None:
        self.config = config
        self.ledger_file = ledger or ledger_path()
        self._lock = threading.RLock()

    # -- ledger ---------------------------------------------------------------

    def _read_ledger(self) -> list[LedgerEntry]:
        if not self.ledger_file.is_file():
            return []
        try:
            raw = yaml.safe_load(self.ledger_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error("❌ ledger unreadable (%s): %s", self.ledger_file, exc)
            raise SessionError(500, "ledger_unreadable", "The sessions ledger could not be read") from exc
        out = []
        for row in raw.get("sessions") or []:
            if isinstance(row, dict) and row.get("path"):
                out.append(LedgerEntry(name=str(row.get("name") or Path(row["path"]).name), path=str(row["path"])))
        return out

    def _write_ledger(self, entries: list[LedgerEntry]) -> None:
        header = "# facilitation-suite ledger — names and folders only (gitignored).\n"
        body = _yaml_dump({"sessions": [{"name": e.name, "path": e.path} for e in entries]})
        atomic_write_text(self.ledger_file, header + body)

    def entries(self) -> list[LedgerEntry]:
        with self._lock:
            return self._read_ledger()

    def entry(self, sid: str) -> LedgerEntry:
        for e in self.entries():
            if e.id == sid:
                return e
        raise SessionError(404, "session_not_found", "No session with that id in the ledger")

    def _add_to_ledger(self, name: str, folder: Path) -> LedgerEntry:
        with self._lock:
            entries = self._read_ledger()
            sid = session_id(folder)
            for e in entries:
                if e.id == sid:
                    return e
            entry = LedgerEntry(name=name, path=str(folder))
            entries.append(entry)
            self._write_ledger(entries)
            logger.info("ℹ️ ledger: added session %s", sid)
            return entry

    def remove(self, sid: str) -> None:
        with self._lock:
            entries = self._read_ledger()
            kept = [e for e in entries if e.id != sid]
            if len(kept) == len(entries):
                raise SessionError(404, "session_not_found", "No session with that id in the ledger")
            self._write_ledger(kept)
            logger.info("ℹ️ ledger: removed session %s (folder kept)", sid)

    # -- folders --------------------------------------------------------------

    def folder(self, sid: str) -> Path:
        return Path(self.entry(sid).path)

    def default_root(self) -> Path:
        root = self.config.session_root.strip()
        return Path(root) if root else Path.home() / "facilitation-sessions"

    def create(self, title: str, workshop: str, folder_name: str, *, date: Optional[datetime] = None,
               duration_minutes: int = 120, root: Optional[str] = None) -> LedgerEntry:
        base = Path(root) if root else self.default_root()
        folder = base / _slug_folder(workshop or "workshop") / _slug_folder(folder_name or title)
        if (folder / SESSION_FILE).exists():
            raise SessionError(409, "session_exists", "That folder already holds a session — add it instead")
        for sub in SUBDIRS:
            (folder / sub).mkdir(parents=True, exist_ok=True)
        session = Session(title=title or "Untitled session", date=date, duration_minutes=duration_minutes)
        self._save_to(folder, session)
        logger.info("✅ created session folder %s", session_id(folder))
        return self._add_to_ledger(title or folder.name, folder)

    def add_existing(self, path: str) -> LedgerEntry:
        folder = Path(path.strip().strip('"'))
        if not (folder / SESSION_FILE).is_file():
            raise SessionError(422, "not_a_session", "No session.yaml in that folder")
        session = self._load_from(folder)
        return self._add_to_ledger(session.title, folder)

    def duplicate(self, sid: str, title: str, folder_name: str, workshop: Optional[str] = None) -> LedgerEntry:
        src = self.folder(sid)
        dest = (src.parent.parent / _slug_folder(workshop) if workshop else src.parent) / _slug_folder(folder_name or title)
        if dest.exists() and any(dest.iterdir()):
            raise SessionError(409, "folder_exists", "The destination folder already exists and is not empty")
        dest.mkdir(parents=True, exist_ok=True)
        for name in DUPLICATE_ENTRIES:
            p = src / name
            if p.is_dir():
                shutil.copytree(p, dest / name, dirs_exist_ok=True)
            elif p.is_file():
                shutil.copy2(p, dest / name)
        for sub in SUBDIRS:
            (dest / sub).mkdir(exist_ok=True)
        session = self._load_from(dest)
        session.title = title or f"{session.title} (copy)"
        session.date = None
        self._save_to(dest, session)
        logger.info("✅ duplicated session %s → %s", sid, session_id(dest))
        return self._add_to_ledger(session.title, dest)

    # -- session.yaml ---------------------------------------------------------

    def _load_from(self, folder: Path) -> Session:
        path = folder / SESSION_FILE
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionError(404, "session_file_missing", "session.yaml is missing from the folder") from exc
        except (OSError, yaml.YAMLError) as exc:
            logger.error("❌ cannot read %s: %s", path, exc)
            raise SessionError(422, "session_file_invalid", "session.yaml could not be read") from exc
        try:
            return parse_session(raw)
        except ValueError as exc:
            raise SessionError(422, "session_file_invalid", f"session.yaml is not valid: {exc}") from exc

    def _save_to(self, folder: Path, session: Session) -> None:
        header = "# facilitation-suite session plan (schema v1) — safe to edit by hand.\n"
        atomic_write_text(folder / SESSION_FILE, header + _yaml_dump(dump_session(session)))

    def load(self, sid: str) -> Session:
        return self._load_from(self.folder(sid))

    def save(self, sid: str, session: Session) -> Session:
        with self._lock:
            folder = self.folder(sid)
            self._save_to(folder, session)
            # keep the ledger's display name in step with the title
            entries = self._read_ledger()
            for e in entries:
                if e.id == sid and e.name != session.title:
                    e.name = session.title
                    self._write_ledger(entries)
                    break
        return session

    def summary(self, entry: LedgerEntry) -> dict[str, Any]:
        folder = Path(entry.path)
        out: dict[str, Any] = {
            "id": entry.id, "name": entry.name, "path": entry.path,
            "crumbs": list(folder.parts[-4:]), "status": "ok",
            "title": entry.name, "date": None, "duration_minutes": None, "sections": 0, "items": 0,
        }
        if not folder.is_dir():
            out["status"] = "missing"
            return out
        try:
            s = self._load_from(folder)
        except SessionError as exc:
            out["status"] = exc.code
            return out
        out.update(title=s.title, date=s.date.isoformat() if s.date else None,
                   duration_minutes=s.duration_minutes, sections=len(s.sections), items=len(s.all_items()))
        return out
