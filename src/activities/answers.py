"""Shared pieces of the plug-ins: free-text answers as they arrive (cards, feed) and one vote per
person (choice, stars, NPS, number)."""

from __future__ import annotations

import re
from typing import Any, Optional

NUMBER = re.compile(r"(?<![\d.,])-?\d+(?:[.,]\d+)?")


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


def numbers(text: str) -> list[float]:
    """Every number in a chat line, in order ("4,5" and "4.5" are one number)."""
    return [float(m.group().replace(",", ".")) for m in NUMBER.finditer(text or "")]


def first_whole(text: str, lo: int, hi: int) -> Optional[int]:
    """The first whole number from ``lo`` to ``hi`` in a chat line, or None."""
    return next((int(n) for n in numbers(text) if n.is_integer() and lo <= n <= hi), None)


def one_vote_each(contributions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[Any]]:
    """``(latest value per named sender, every anonymous value)``: a later answer replaces an earlier one."""
    latest: dict[str, Any] = {}
    anonymous: list[Any] = []
    for c in contributions:
        if c["sender"]:
            latest[c["sender"]] = c["value"]
        else:
            anonymous.append(c["value"])
    return latest, anonymous


def names_with(latest: dict[str, Any], hit: Any) -> list[str]:
    """The people whose counted vote matches ``hit`` (a value, or a predicate on the value)."""
    test = hit if callable(hit) else (lambda v: v == hit)
    return [n for n, v in latest.items() if test(v)]
