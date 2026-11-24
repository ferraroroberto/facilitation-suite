"""NPS: how likely, from 0 to 10, someone is to recommend; the first whole number from 0 to 10 counts.

Promoters answer 9 or 10, passives 7 or 8, detractors 0 to 6; the score is the
share of promoters minus the share of detractors, from -100 to 100. One vote
per person: a later answer replaces an earlier one.
"""

from __future__ import annotations

from typing import Any, Optional

from src.activities.answers import first_whole, names_with, one_vote_each

GROUPS = (("promoters", 9, 10), ("passives", 7, 8), ("detractors", 0, 6))


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    n = first_whole(message.get("text") or "", 0, 10)
    return None if n is None else {"value": n, "sender": message.get("sender", "")}


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    latest, anonymous = one_vote_each(contributions)
    votes = list(latest.values()) + anonymous
    groups: dict[str, dict[str, Any]] = {}
    for name, lo, hi in GROUPS:
        count = sum(1 for v in votes if lo <= v <= hi)
        groups[name] = {"count": count, "percent": round(100 * count / len(votes)) if votes else 0,
                        "names": names_with(latest, lambda v, lo=lo, hi=hi: lo <= v <= hi)}
    score = round(100 * (groups["promoters"]["count"] - groups["detractors"]["count"]) / len(votes)) if votes else None
    return {"score": score, **groups, "bins": [{"value": v, "count": votes.count(v)} for v in range(11)],
            "votes": len(votes), "answers": len(contributions), "people": len(latest)}


def signed(score: Any) -> str:
    return f"{score:+d}" if isinstance(score, int) else "–"


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    return {
        "summary": f"{result.get('votes', 0)} votes · NPS {signed(result.get('score'))}",
        "top_label": "Votes",
        "top": [{"label": name.capitalize(), "count": (result.get(name) or {}).get("count", 0)} for name, _, _ in GROUPS],
        "tile": signed(result.get("score")),
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return str(parsed.get("value", ""))
