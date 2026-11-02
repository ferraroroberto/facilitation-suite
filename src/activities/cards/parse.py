"""Cards: each answer is a card with the person's name; the latest stay on stage."""

from __future__ import annotations

from typing import Any

from src.activities.answers import count_report, keep_count, parse_text, tally, text_value

parse = parse_text
value = text_value


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    keep = keep_count(options, "max_cards", 12)
    return {"cards": contributions[-keep:], **tally(contributions)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    return count_report(result, "card")
