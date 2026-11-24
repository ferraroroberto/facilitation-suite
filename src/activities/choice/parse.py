"""Choice: one of the listed choices, or several when the item allows more than one.

The choices are lettered A, B, C… on the stage and on the phones. A chat line
answers with letters or numbers alone ("B", "b, d", "1 y 3", "A and C"), with a
letter in front of a comment ("B) because…"), or with a choice's own words
("Remote"). With one choice allowed only the first counts. One vote per person:
a later answer replaces an earlier one.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.activities.answers import names_with, one_vote_each
from src.text import fold

MAX_CHOICES = 20  # A to T, so "y" stays free to mean "and" in a Spanish chat line
CONNECTORS = {"and", "y"}
LEADING_KEY = re.compile(r"^\s*([a-z]|\d{1,2})\s*[).:\-]")


def choices(options: dict[str, Any]) -> list[str]:
    """The choices in order: a list (an importer's), or the editor's comma-separated text."""
    raw = options.get("choices") or ""
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return [str(c).strip() for c in items if str(c).strip()][:MAX_CHOICES]


def keys(n: int) -> list[str]:
    return [chr(ord("A") + i) for i in range(n)]


def _key_of(token: str, n: int) -> Optional[str]:
    if len(token) == 1 and token.isalpha() and ord(token) - ord("a") < n:
        return token.upper()
    if token.isdigit() and 1 <= int(token) <= n:
        return keys(n)[int(token) - 1]
    return None


def _words(text: str) -> str:
    return f" {re.sub(r'[^0-9a-z]+', ' ', fold(text)).strip()} "


def picked(text: str, labels: list[str]) -> list[str]:
    """The choice keys a chat line names, in the order it names them (empty: none)."""
    n = len(labels)
    tokens = [t for t in re.findall(r"[0-9a-z]+", fold(text)) if t not in CONNECTORS]
    found = [_key_of(t, n) for t in tokens]
    if tokens and all(found):
        return list(dict.fromkeys(k for k in found if k))
    lead = LEADING_KEY.match(fold(text))
    if lead and _key_of(lead.group(1), n):
        return [str(_key_of(lead.group(1), n))]
    words = _words(text)
    hits = sorted((words.find(_words(label)), i) for i, label in enumerate(labels) if _words(label).strip())
    return [keys(n)[i] for pos, i in hits if pos >= 0]


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    named = picked(message.get("text") or "", choices(options))
    if not named:
        return None
    return {"value": named if options.get("multiple") else named[:1], "sender": message.get("sender", "")}


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    labels = choices(options)
    latest, anonymous = one_vote_each(contributions)
    votes = list(latest.values()) + anonymous
    bars = []
    for key, label in zip(keys(len(labels)), labels, strict=True):
        count = sum(1 for v in votes if key in v)
        bars.append({"key": key, "label": label, "count": count,
                     "percent": round(100 * count / len(votes)) if votes else 0,
                     "names": names_with(latest, lambda v, k=key: k in v)})
    return {"bars": bars, "multiple": bool(options.get("multiple")), "votes": len(votes),
            "answers": len(contributions), "people": len(latest)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    bars = result.get("bars") or []
    lead = max(bars, key=lambda b: b["count"], default=None)
    lead = lead if lead and lead["count"] else None
    return {
        "summary": f"{result.get('votes', 0)} votes" + (f" · most: {lead['key']} {lead['label']}" if lead else ""),
        "top_label": "Votes",
        "top": [{"label": f"{b['key']} · {b['label']}", "count": b["count"]} for b in bars],
        "tile": lead["key"] if lead else "–",
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return ", ".join(parsed.get("value") or [])
