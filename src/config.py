"""Project configuration — ``config/config.json`` with a committed sample twin.

The real file is gitignored (it carries this machine's paths); the committed
``config/config.sample.json`` documents every key. A missing real file falls
back to the sample so a fresh clone boots, and every key has a code-level
default so a partial file never crashes startup.

Environment overrides (the test harness and the e2e instance use them so the
gate never reads or writes the real files):

- ``FS_CONFIG_PATH`` — the config file.
- ``FS_LEDGER_PATH`` — the sessions ledger (default ``sessions.local.yaml``).
- ``FS_DATA_DIR``    — the machine-local ``data/`` dir (logs, caches).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_SAMPLE_PATH = CONFIG_DIR / "config.sample.json"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "sessions.local.yaml"
DEFAULT_PORT = 8449



@dataclass(frozen=True)
class ObsConfig:
    """obs-websocket v5 (built into OBS 28+). ``password`` is a secret: it lives
    in the gitignored config only and is never logged."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""


@dataclass(frozen=True)
class ReaderConfig:
    """The Zoom chat reader process (``python -m src.chat.reader``)."""

    enabled: bool = True
    poll_ms: int = 500
    window_class: str = "ZConfChatPopupContainerWndClass"
    window_title: str = "Meeting chat"


@dataclass(frozen=True)
class AppConfig:
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    # Default parent folder for new sessions: <session_root>/<workshop>/<session>.
    session_root: str = ""
    stage_display: int = 2
    obs: ObsConfig = field(default_factory=ObsConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    source: str = "defaults"


def _build[T](cls: type[T], raw: Any) -> T:
    """Dataclass from a JSON object: known keys only, nested dataclasses too,
    a wrong-typed value falls back to the default (logged, never fatal)."""
    if not isinstance(raw, dict):
        return cls()
    defaults = cls()
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in raw:
            continue
        value = raw[f.name]
        current = getattr(defaults, f.name)
        if is_dataclass(current):
            kwargs[f.name] = _build(type(current), value)
        elif isinstance(value, type(current)) and not (isinstance(value, bool) and not isinstance(current, bool)):
            kwargs[f.name] = value
        else:
            logger.warning("⚠️ config: %s.%s has the wrong type (%r) — using the default", cls.__name__, f.name, value)
    return cls(**kwargs)


def config_path() -> Path:
    override = os.environ.get("FS_CONFIG_PATH", "").strip()
    return Path(override) if override else CONFIG_PATH


def ledger_path() -> Path:
    override = os.environ.get("FS_LEDGER_PATH", "").strip()
    return Path(override) if override else DEFAULT_LEDGER_PATH


def data_dir() -> Path:
    override = os.environ.get("FS_DATA_DIR", "").strip()
    return Path(override) if override else PROJECT_ROOT / "data"


def load_config() -> AppConfig:
    """The real config, else the sample, else code defaults — said once in the log."""
    path = config_path()
    candidates = [path] if os.environ.get("FS_CONFIG_PATH") else [path, CONFIG_SAMPLE_PATH]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("❌ config: cannot read %s (%s) — trying the next source", candidate, exc)
            continue
        cfg = _build(AppConfig, raw)
        return AppConfig(**{**cfg.__dict__, "source": str(candidate)})
    logger.warning("⚠️ config: no config file found — running on code defaults")
    return AppConfig()
