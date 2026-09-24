"""Activity plug-ins (epic §8): one folder per type under ``app/activities/<type>/``.

A plug-in folder holds ``editor.json`` (label, icon, the options the Plan
editor shows, their defaults), and from step 7 ``parse.py`` (chat message →
contribution, contributions → result) and ``stage.js`` / ``stage.css`` (render
the result on the stage). Adding a type = adding a folder; nothing else
enumerates types.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from functools import cache, lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

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


@cache
def parser(activity_type: str) -> Optional[ModuleType]:
    """The type's ``parse.py`` (``parse(message, options)`` + ``aggregate(contributions, options)``)."""
    if activity_type not in editors():
        return None
    path = ACTIVITIES_DIR / activity_type / "parse.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"fs_activity_{activity_type}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 — one broken plug-in must not take the live session down
        logger.exception("❌ activity %s: parse.py failed to load", activity_type)
        return None
    return module


def result_for(activity_type: str, options: dict[str, Any], messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Parse every message and aggregate: the result the stage renders (None: no parser)."""
    mod = parser(activity_type)
    if mod is None:
        return None
    opts = options_with_defaults(activity_type, options)
    contributions = []
    for m in messages:
        try:
            c = mod.parse(m, opts)
        except Exception:  # noqa: BLE001 — a message that breaks a parser is skipped, not fatal
            logger.exception("❌ activity %s: parse failed on message %s", activity_type, m.get("id"))
            continue
        if c is not None:
            contributions.append(c)
    try:
        return mod.aggregate(contributions, opts)
    except Exception:  # noqa: BLE001
        logger.exception("❌ activity %s: aggregate failed", activity_type)
        return None


def preview(activity_type: str, options: dict[str, Any], answers: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
    """A result from the type's sample answers (the Plan tab's stage preview)."""
    people = ["Alex", "Sam", "Jordan", "Casey", "Robin", "Jamie", "Taylor", "Morgan", "Avery", "Riley", "Quinn", "Drew"]
    texts = answers if answers is not None else (editors().get(activity_type) or {}).get("samples", [])
    msgs = [{"id": i + 1, "sender": people[i % len(people)], "text": t, "time": "18:41", "received_at": 0} for i, t in enumerate(texts)]
    return result_for(activity_type, options, msgs)
