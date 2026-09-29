"""Settings written from the app: a partial update of ``config/config.json``.

Keys the app does not know are kept; the write is atomic. The OBS password
is a secret: it is written when given and never read back out (the API only
says whether one is set).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.config import CONFIG_SAMPLE_PATH, AppConfig, config_path, load_config
from src.errors import DomainError
from src.sessions.store import atomic_write_text

logger = logging.getLogger(__name__)


class SettingsError(DomainError):
    """Raised when the settings file can't be safely read for merging."""


def _read_raw() -> dict[str, Any]:
    path = config_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("❌ settings: %s exists but is unreadable (%s) — refusing to write over it", path, exc)
            raise SettingsError(
                500, "config_unreadable", f"{path} exists but could not be read — fix it by hand, then try again"
            ) from exc
    if os.environ.get("FS_CONFIG_PATH", "").strip():
        return {}  # an explicit override path with nothing there yet — never fall back to the sample
    if CONFIG_SAMPLE_PATH.is_file():
        try:
            return json.loads(CONFIG_SAMPLE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("❌ settings: %s unreadable (%s)", CONFIG_SAMPLE_PATH, exc)
    return {}


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def update(patch: dict[str, Any], *, replace: tuple[str, ...] = ()) -> AppConfig:
    """Merge ``patch`` into the config file and return the reloaded config. The top-level
    keys in ``replace`` are written whole instead of merged (a value that can lose keys)."""
    raw = _merge(_read_raw(), {k: v for k, v in patch.items() if k not in replace})
    raw.update({k: v for k, v in patch.items() if k in replace})
    atomic_write_text(config_path(), json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    logger.info("ℹ️ settings saved: %s", ", ".join(sorted(patch)))
    return load_config()
