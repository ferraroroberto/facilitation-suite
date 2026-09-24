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


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    n = result.get("answers", 0)
    return {"summary": f"{n} card{'' if n == 1 else 's'}", "top_label": "", "top": [], "tile": str(n)}


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return parsed.get("text", "")
