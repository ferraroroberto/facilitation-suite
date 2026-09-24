"""Activity plug-ins (epic §8): one folder per type under ``app/activities/<type>/``.

A plug-in folder holds ``editor.json`` (label, icon, the options the Plan
editor shows, their defaults), and from step 7 ``parse.py`` (chat message →
contribution, contributions → result) and ``stage.js`` / ``stage.css`` (render
the result on the stage). Adding a type = adding a folder; nothing else
enumerates types.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACTIVITIES_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "activities"


@lru_cache(maxsize=1)
def editors() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(ACTIVITIES_DIR.glob("*/editor.json")):
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("❌ activity %s: editor.json unreadable (%s) — type skipped", f.parent.name, exc)
            continue
        if spec.get("type") != f.parent.name:
            logger.error("❌ activity %s: editor.json type %r does not match its folder — skipped", f.parent.name, spec.get("type"))
            continue
        spec.setdefault("capture", True)
        spec["has_stage"] = (f.parent / "stage.js").is_file()
        out[spec["type"]] = spec
    return dict(sorted(out.items(), key=lambda kv: kv[1].get("order", 99)))


def defaults(activity_type: str) -> dict[str, Any]:
    spec = editors().get(activity_type) or {}
    return {o["key"]: o.get("default") for o in spec.get("options", [])}


def options_with_defaults(activity_type: str, options: dict[str, Any]) -> dict[str, Any]:
    return {**defaults(activity_type), **(options or {})}
