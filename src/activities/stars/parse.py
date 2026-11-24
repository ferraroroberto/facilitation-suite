"""Stars: a rating from 1 to 5, as a number ("4", "4/5", "4 stars") or as stars typed ("****").

One vote per person: a later answer replaces an earlier one. The result is the
average and the spread: how many gave each number of stars, and the standard
deviation.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.activities.answers import first_whole, names_with, one_vote_each

STARS = 5
STAR_CHARS = "*★⭐"
TYPED = re.compile(r"^[\s*★⭐]+$")


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = message.get("text") or ""
    if TYPED.match(text):
        n = sum(text.count(c) for c in STAR_CHARS)
        stars: Optional[int] = n if 1 <= n <= STARS else None
    else:
        stars = first_whole(text, 1, STARS)
    return None if stars is None else {"value": stars, "sender": message.get("sender", "")}


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    latest, anonymous = one_vote_each(contributions)
    votes = list(latest.values()) + anonymous
    bins = [{"value": v, "count": votes.count(v), "names": names_with(latest, v)} for v in range(STARS, 0, -1)]
    average = sum(votes) / len(votes) if votes else None
    spread = (sum((v - average) ** 2 for v in votes) / len(votes)) ** 0.5 if average is not None else None
    return {"bins": bins, "average": None if average is None else round(average, 2),
            "spread": None if spread is None else round(spread, 2),
            "votes": len(votes), "answers": len(contributions), "people": len(latest)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    avg, spread = result.get("average"), result.get("spread")
    shown = f"{avg:.1f}" if isinstance(avg, (int, float)) else "–"
    plus = f" ± {spread:.1f}" if isinstance(spread, (int, float)) else ""
    return {
        "summary": f"{result.get('votes', 0)} votes · average {shown}{plus}",
        "top_label": "Votes",
        "top": [{"label": "★" * b["value"], "count": b["count"]} for b in result.get("bins") or []],
        "tile": f"{shown} ★",
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return str(parsed.get("value", ""))
