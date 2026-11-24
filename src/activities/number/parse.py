"""Number: the first number in a chat line counts ("42", "about 3,5 hours", "-2").

With a lowest or highest set, a number outside them is skipped and the next one
tried. One vote per person: a later answer replaces an earlier one. The result
is a histogram and the median: one bar per whole number when the answers are
whole and span at most a dozen, else at most eight ranges of a round width.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Optional

from src.activities.answers import numbers, one_vote_each

WHOLE_BARS = 12  # up to this many whole numbers in a row get a bar each; wider answers get ranges
RANGES = 8  # at most this many ranges, so each label stays readable on the stage


def bound(options: dict[str, Any], key: str) -> Optional[float]:
    raw = options.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def shown(x: float) -> str:
    """A number as people write it: whole without decimals, else up to two."""
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}".rstrip("0").rstrip(".")


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    lo, hi = bound(options, "min"), bound(options, "max")
    for n in numbers(message.get("text") or ""):
        if (lo is None or n >= lo) and (hi is None or n <= hi):
            return {"value": int(n) if n.is_integer() else n, "sender": message.get("sender", "")}
    return None


def step_for(span: float) -> float:
    """A round range width (1, 2 or 5 times a power of ten) giving at most ``RANGES`` ranges."""
    raw = span / RANGES
    power = 10 ** math.floor(math.log10(raw))
    return next(m * power for m in (1, 2, 5, 10) if m * power >= raw)


def bars(votes: list[float]) -> list[dict[str, Any]]:
    """The histogram of sorted votes: a bar per whole number, else round ranges (the last one closed)."""
    if not votes:
        return []
    lo, hi = votes[0], votes[-1]
    whole = all(float(v).is_integer() for v in votes)
    if whole and hi - lo < WHOLE_BARS:
        return [{"label": shown(v), "count": votes.count(v)} for v in range(int(lo), int(hi) + 1)]
    step = step_for(hi - lo)
    start = math.floor(lo / step) * step
    n = max(1, math.ceil((hi - start) / step - 1e-9))
    out = []
    for i in range(n):
        a, b = round(start + i * step, 9), round(start + (i + 1) * step, 9)
        last = i == n - 1
        top = b if last or not (whole and step.is_integer()) else b - 1  # whole answers: "0–4", "5–9"
        out.append({"label": f"{shown(a)}–{shown(top)}",
                    "count": sum(1 for v in votes if a <= v < b or (last and v == b))})
    return out


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    latest, anonymous = one_vote_each(contributions)
    votes = sorted(list(latest.values()) + anonymous)
    mid = median(votes) if votes else None
    return {"bars": bars(votes), "median": None if mid is None else round(mid, 2),
            "lowest": votes[0] if votes else None, "highest": votes[-1] if votes else None,
            "unit": str(options.get("unit") or ""), "votes": len(votes), "answers": len(contributions),
            "people": len(latest)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    mid = result.get("median")
    text = shown(mid) if isinstance(mid, (int, float)) else "–"
    return {
        "summary": f"{result.get('votes', 0)} answers · median {text}",
        "top_label": "Answers",
        "top": [{"label": b["label"], "count": b["count"]} for b in result.get("bars") or []],
        "tile": text,
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    v = parsed.get("value")
    return shown(v) if isinstance(v, (int, float)) else ""
