"""Shared pieces of the plug-ins that show free-text answers as they arrive (cards, feed)."""

from __future__ import annotations

from typing import Any, Optional


def parse_text(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    return {"id": message.get("id"), "text": text, "sender": message.get("sender", "")}


def keep_count(options: dict[str, Any], key: str, default: int) -> int:
    """How many of the latest answers stay on stage (at least one; ``default`` when unusable)."""
    try:
        return max(1, int(options.get(key, default)))
    except (TypeError, ValueError):
        return default


def tally(contributions: list[dict[str, Any]]) -> dict[str, int]:
    """The answer and distinct-sender counts every free-text result carries."""
    senders = {c["sender"] for c in contributions if c["sender"]}
    return {"answers": len(contributions), "people": len(senders)}


def count_report(result: dict[str, Any], noun: str) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    n = result.get("answers", 0)
    return {"summary": f"{n} {noun}{'' if n == 1 else 's'}", "top_label": "", "top": [], "tile": str(n)}


def text_value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return parsed.get("text", "")
