"""Word cloud: answers → terms → a weighted list of words and phrases.

How an answer becomes cloud entries is the ``terms`` option:

- ``auto`` (the default, and what a session without the option gets): answers
  of up to three words stay one phrase ("no agenda", "miedo a fallar"); longer
  answers are split into words, dropping filler words (Spanish and/or English
  stopword lists) — so a sentence still feeds the cloud.
- ``verbatim``: each answer is one entry as typed — trimmed, whitespace
  collapsed, and the punctuation at its ends dropped ("¡Sí!" → "Sí"), so
  "saying yes!" and "Saying yes" group together. No stopwords.
- ``words``: every answer, even a short one, is split into words minus
  stopwords (the classic cloud: "no agenda" → "agenda").

In every mode an answer made only of laughter and fillers ("jajaja", "lol",
"xd", "ok") is dropped (``auto`` and ``words`` also drop them inside answers).
With ``merge_variants``: case, accents and simple plurals are grouped
("Reunión", "reuniones" → one entry) and the most common spelling is shown.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
FILLER = re.compile(r"^(?:(?:ja|je|ji|ha|he|jo)+h?|lol+|xd+|jaj+|hah+|mm+|eh+|ok+|okay)$")
TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)
SPACES = re.compile(r"\s+")
PHRASE_MAX_WORDS = 3
MAX_WORDS = 60


def fold(text: str) -> str:
    """Lower-case without accents (grouping key)."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _stopwords(which: str) -> frozenset[str]:
    langs = {"es": ["es"], "en": ["en"], "both": ["es", "en"]}.get(which, [])
    words: set[str] = set()
    for lang in langs:
        path = HERE / f"stopwords_{lang}.txt"
        words |= {fold(w.strip()) for w in path.read_text(encoding="utf-8").split() if w.strip()}
    return frozenset(words)


def _trim(text: str) -> str:
    """Drop spaces and punctuation (Unicode category P*: "¡", "!", "…", quotes) from both ends."""
    def edge(c: str) -> bool:
        return c.isspace() or unicodedata.category(c).startswith("P")
    start, end = 0, len(text)
    while start < end and edge(text[start]):
        start += 1
    while end > start and edge(text[end - 1]):
        end -= 1
    return text[start:end]


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = message.get("text") or ""
    tokens = [t.strip("'’-") for t in TOKEN.findall(text)]
    tokens = [t for t in tokens if t and not FILLER.match(fold(t))]
    if not tokens:
        return None
    mode = str(options.get("terms") or "auto")
    if mode == "verbatim":
        terms = [_trim(SPACES.sub(" ", text))]
    elif mode != "words" and len(tokens) <= PHRASE_MAX_WORDS:
        terms = [" ".join(tokens)]
    else:
        stop = _stopwords(str(options.get("stopwords", "es")))
        terms = [t for t in tokens if fold(t) not in stop and not t.isdigit()]
    return {"terms": terms, "sender": message.get("sender", "")} if terms else None


def _singular(key: str, keys: set[str]) -> str:
    """Merge a simple plural into its singular when the singular is present."""
    words = key.split(" ")
    last = words[-1]
    for cut in ("es", "s"):
        if last.endswith(cut) and len(last) > len(cut) + 2:
            cand = " ".join(words[:-1] + [last[: -len(cut)]])
            if cand in keys:
                return cand
    return key


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    merge = bool(options.get("merge_variants", True))
    spellings: dict[str, Counter] = {}
    names: dict[str, list[str]] = {}
    order: dict[str, int] = {}
    for c in contributions:
        for term in c["terms"]:
            key = fold(term) if merge else term
            spellings.setdefault(key, Counter())[term.lower() if merge else term] += 1
            names.setdefault(key, [])
            if c["sender"] and c["sender"] not in names[key]:
                names[key].append(c["sender"])
            order.setdefault(key, len(order))
    if merge:
        keys = set(spellings)
        for key in sorted(keys, key=len, reverse=True):
            target = _singular(key, keys)
            if target != key and key in spellings:
                spellings[target].update(spellings.pop(key))
                names[target] += [n for n in names.pop(key) if n not in names[target]]
                order[target] = min(order[target], order.pop(key))
    words = [{
        "key": key,
        "text": sp.most_common(1)[0][0],
        "count": sum(sp.values()),
        "names": names[key],
        "first": order[key],
    } for key, sp in spellings.items()]
    words.sort(key=lambda w: (-w["count"], w["first"]))
    senders = {c["sender"] for c in contributions if c["sender"]}
    return {"words": words[:MAX_WORDS], "answers": len(contributions), "people": len(senders)}


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    words = result.get("words") or []
    return {
        "summary": f"{result.get('answers', 0)} answers · word cloud",
        "top_label": "Top words",
        "top": [{"label": w["text"], "count": w["count"]} for w in words[:6]],
        "tile": words[0]["text"] if words else "",
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report."""
    return ", ".join(parsed.get("terms") or [])
