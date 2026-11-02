"""Feed: answers flow in as bubbles, newest first and largest."""

from __future__ import annotations

from typing import Any

from src.activities.answers import count_report, keep_count, parse_text, tally, text_value

parse = parse_text
value = text_value


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    keep = keep_count(options, "max_items", 16)
    return {"items": list(reversed(contributions[-keep:])), **tally(contributions)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    return count_report(result, "idea")
