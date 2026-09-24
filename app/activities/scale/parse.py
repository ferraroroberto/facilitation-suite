"""Scale: the first number in range counts — or a keyword from the list.

"4", "un 4 hoy", "4/5" → 4. With keywords (lowest to highest, e.g. "storm,
rain, cloudy, sunny spells, sun") a message naming one counts as its
position; the longest keyword wins ("sunny spells" before "sun"). Emoji are
lost in Zoom's chat, so prompts ask for a number or a word. One vote per
person: a later answer replaces an earlier one.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

NUMBER = re.compile(r"(?<![\d.,])-?\d+(?:[.,]\d+)?")


def fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _range(options: dict[str, Any]) -> tuple[int, int]:
    try:
        lo, hi = int(options.get("min", 1)), int(options.get("max", 5))
    except (TypeError, ValueError):
        lo, hi = 1, 5
    return (lo, hi) if lo < hi else (1, 5)


def keywords(options: dict[str, Any]) -> list[str]:
    return [k.strip() for k in str(options.get("keywords") or "").split(",") if k.strip()]


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = message.get("text") or ""
    lo, hi = _range(options)
    for m in NUMBER.finditer(text):
        value = float(m.group().replace(",", "."))
        if value.is_integer() and lo <= value <= hi:
            return {"value": int(value), "sender": message.get("sender", "")}
    words = keywords(options)
    folded = f" {re.sub(r'[^0-9a-z]+', ' ', fold(text))} "
    for idx in sorted(range(len(words)), key=lambda i: -len(words[i])):
        kw = re.sub(r"[^0-9a-z]+", " ", fold(words[idx])).strip()
        if kw and f" {kw} " in folded and lo + idx <= hi:
            return {"value": lo + idx, "sender": message.get("sender", "")}
    return None


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    lo, hi = _range(options)
    words = keywords(options)
    latest: dict[str, int] = {}
    anonymous: list[int] = []
    for c in contributions:
        if c["sender"]:
            latest[c["sender"]] = c["value"]
        else:
            anonymous.append(c["value"])
    votes = list(latest.values()) + anonymous
    bins = []
    for v in range(lo, hi + 1):
        idx = v - lo
        bins.append({"value": v, "label": words[idx] if idx < len(words) else str(v),
                     "count": sum(1 for x in votes if x == v),
                     "names": [n for n, x in latest.items() if x == v]})
    average = round(sum(votes) / len(votes), 2) if votes else None
    return {"bins": bins, "average": average, "votes": len(votes), "answers": len(contributions), "people": len(latest)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    avg = result.get("average")
    shown = f"{avg:.1f}" if isinstance(avg, (int, float)) else "–"
    return {
        "summary": f"{result.get('answers', 0)} answers · average {shown}",
        "top_label": "Votes",
        "top": [{"label": f"{b['value']} · {b['label']}" if b["label"] != str(b["value"]) else str(b["value"]), "count": b["count"]}
                for b in reversed(result.get("bins") or [])],
        "tile": shown,
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return str(parsed.get("value", ""))
