"""Feed: answers flow in as bubbles, newest first and largest."""

from __future__ import annotations

from typing import Any, Optional


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    return {"id": message.get("id"), "text": text, "sender": message.get("sender", "")}


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    try:
        keep = max(1, int(options.get("max_items", 16)))
    except (TypeError, ValueError):
        keep = 16
    senders = {c["sender"] for c in contributions if c["sender"]}
    return {"items": list(reversed(contributions[-keep:])), "answers": len(contributions), "people": len(senders)}
