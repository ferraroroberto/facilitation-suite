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


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    n = result.get("answers", 0)
    return {"summary": f"{n} idea{'' if n == 1 else 's'}", "top_label": "", "top": [], "tile": str(n)}


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return parsed.get("text", "")
