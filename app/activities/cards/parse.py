"""Cards: each answer is a card with the person's name; the latest stay on stage."""

from __future__ import annotations

from typing import Any, Optional


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    return {"id": message.get("id"), "text": text, "sender": message.get("sender", "")}


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    try:
        keep = max(1, int(options.get("max_cards", 12)))
    except (TypeError, ValueError):
        keep = 12
    senders = {c["sender"] for c in contributions if c["sender"]}
    return {"cards": contributions[-keep:], "answers": len(contributions), "people": len(senders)}
