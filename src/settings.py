"""Settings written from the app: a partial update of ``config/config.json``.

Keys the app does not know are kept; the write is atomic. The OBS password
is a secret: it is written when given and never read back out (the API only
says whether one is set).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config import CONFIG_SAMPLE_PATH, AppConfig, config_path, load_config
from src.sessions.store import atomic_write_text

logger = logging.getLogger(__name__)


def _read_raw() -> dict[str, Any]:
    for path in (config_path(), CONFIG_SAMPLE_PATH):
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("❌ settings: %s unreadable (%s)", path, exc)
    return {}


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def update(patch: dict[str, Any]) -> AppConfig:
    """Merge ``patch`` into the config file and return the reloaded config."""
    raw = _merge(_read_raw(), patch)
    atomic_write_text(config_path(), json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    logger.info("ℹ️ settings saved: %s", ", ".join(sorted(patch)))
    return load_config()
