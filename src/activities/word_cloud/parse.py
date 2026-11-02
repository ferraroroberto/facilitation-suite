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
("Reunión", "reuniones" → one entry) and the most common spelling is shown —
lower-cased in ``auto`` and ``words``; in ``verbatim`` the most common spelling
as typed ("I stop listening" ×2 beats "i stop listening" ×1; a tie goes to the
first seen).

Four options refine this, all off by default:

- ``split_ideas``: an answer is first split at ``+ , ; / &`` and line breaks
  ("calm + humour" → two entries); each part then goes through the mode above.
  ``split_and`` (its own switch) also splits at " y ", " e " and " and ".
- ``long_answers`` (``auto`` only) decides an answer or part longer than three
  words: ``split`` (the default: words minus stopwords), ``whole`` (one phrase,
  wrapped by the stage) or ``keyword`` (its longest non-filler word — one per
  answer, however many long parts it has).
- ``merge_near``: after case, accents and plurals, an entry joins a more
  frequent (or, at a tie, earlier) entry of the cloud one typo away (two from
  nine letters); words under five letters never merge ("pan" stays apart from
  "paz"). The entries it absorbed are listed in its ``variants``.

An answer made only of emoticons (":P", ":-)", "<3") is dropped like laughter,
and an emoticon inside an answer is ignored, never shown as the letter "p".
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from src.text import fold

HERE = Path(__file__).resolve().parent
FILLER = re.compile(r"^(?:(?:ja|je|ji|ha|he|jo)+h?|lol+|xd+|jaj+|hah+|mm+|eh+|ok+|okay)$")
TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)
SPACES = re.compile(r"\s+")
# Emoticons as whitespace-separated pieces: eyes, optional nose, mouth; the reverse; hearts; ^^; o_O.
EMOTICON = re.compile(r"(?<!\S)(?:[:;=8][-'^o]?[()\[\]DPpOo3/\\|*$@]+|[()]+[-']?[:;=]|<3+|\^_*\^|[oO0-]_+[oO0-])(?!\S)")
IDEAS = re.compile(r"[+,;/&\r\n]+")
CONJUNCTIONS = frozenset({"y", "e", "and"})
AND = re.compile(r"\s(?:y|e|and)\s", re.IGNORECASE)
PHRASE_MAX_WORDS = 3
MAX_WORDS = 60


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


def _tokens(text: str) -> list[str]:
    tokens = [t.strip("'’-") for t in TOKEN.findall(text)]
    return [t for t in tokens if t and not FILLER.match(fold(t))]


def _parts(text: str, options: dict[str, Any]) -> list[str]:
    """The answer's ideas: split at the separators the options switch on."""
    parts = IDEAS.split(text) if options.get("split_ideas") else [text]
    if options.get("split_and"):
        parts = [q for p in parts for q in AND.split(p)]
    return [p for p in parts if _tokens(p)]


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    parts = _parts(EMOTICON.sub(" ", message.get("text") or ""), options)
    mode = str(options.get("terms") or "auto")
    long_answers = str(options.get("long_answers") or "split")
    terms: list[str] = []
    keywords: list[str] = []
    for part in parts:
        tokens = _tokens(part)
        if mode == "verbatim":
            terms.append(_trim(SPACES.sub(" ", part)))
        elif mode != "words" and (len(tokens) <= PHRASE_MAX_WORDS or long_answers == "whole"):
            terms.append(" ".join(tokens))
        else:
            stop = _stopwords(str(options.get("stopwords", "es")))
            words = [t for t in tokens if fold(t) not in stop and not t.isdigit()]
            if mode != "words" and long_answers == "keyword":
                keywords += words
            else:
                terms += words
    if keywords:  # one per answer: the longest, the first at a tie
        terms.append(max(keywords, key=len))
    if not terms:
        return None
    out: dict[str, Any] = {"terms": terms, "sender": message.get("sender", "")}
    if len(parts) > 1 and len(terms) == len(parts):
        out["ideas"] = True  # each idea of the answer became one entry: the answer is still shown whole
    return out


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


def _distance(a: str, b: str) -> int:
    """Edits from ``a`` to ``b``: insert, delete, substitute, or swap two neighbours ("aopyo" → "apoyo")."""
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            d = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                d = min(d, prev2[j - 2] + 1)
            cur.append(d)
        prev2, prev = prev, cur
    return prev[-1]


def _near(a: str, b: str) -> bool:
    """One typo apart from five letters, two from nine; the shorter key's length decides."""
    n = min(len(a), len(b))
    limit = 2 if n >= 9 else 1 if n >= 5 else 0
    return limit > 0 and abs(len(a) - len(b)) <= limit and _distance(a, b) <= limit


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    merge = bool(options.get("merge_variants", True))
    verbatim = str(options.get("terms") or "auto") == "verbatim"
    spellings: dict[str, Counter] = {}
    names: dict[str, list[str]] = {}
    order: dict[str, int] = {}
    seen: dict[str, int] = {}  # each spelling's first appearance (verbatim's tie-break)
    for c in contributions:
        for term in c["terms"]:
            key = fold(term) if merge else term
            spelling = term.lower() if merge and not verbatim else term
            spellings.setdefault(key, Counter())[spelling] += 1
            seen.setdefault(spelling, len(seen))
            names.setdefault(key, [])
            if c["sender"] and c["sender"] not in names[key]:
                names[key].append(c["sender"])
            order.setdefault(key, len(order))

    def absorb(target: str, key: str) -> None:
        spellings[target].update(spellings.pop(key))
        names[target] += [n for n in names.pop(key) if n not in names[target]]
        order[target] = min(order[target], order.pop(key))

    if merge:
        keys = set(spellings)
        for key in sorted(keys, key=len, reverse=True):
            target = _singular(key, keys)
            if target != key and key in spellings:
                absorb(target, key)
    variants: dict[str, list[str]] = {}
    if merge and options.get("merge_near"):
        kept: list[str] = []  # strongest first, so an entry only ever joins a stronger one already kept
        for key in sorted(spellings, key=lambda k: (-sum(spellings[k].values()), order[k])):
            target = min((k for k in kept if _near(key, k)), key=lambda k: _distance(key, k), default=None)
            if target is None:
                kept.append(key)
            else:
                absorb(target, key)
                variants.setdefault(target, []).append(key)

    def shown(sp: Counter) -> str:
        if verbatim:  # as typed: the most common original spelling, a tie to the first seen
            return min(sp, key=lambda s: (-sp[s], seen[s]))
        return sp.most_common(1)[0][0]

    words = [{
        "key": key,
        "text": shown(sp),
        "count": sum(sp.values()),
        "names": names[key],
        "first": order[key],
        **({"variants": variants[key]} if key in variants else {}),
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
    """One answer's terms, the report's fallback when none of them is in the cloud."""
    return ", ".join(parsed.get("terms") or [])


def _entries(terms: list[str], result: dict[str, Any]) -> list[dict[str, Any]]:
    """The cloud entries ``terms`` count towards, through case, accents, plurals and near-spellings."""
    by_key = {k: w for w in result.get("words") or [] for k in [w["key"], *w.get("variants", [])]}
    out: list[dict[str, Any]] = []
    for term in terms:
        key = next((k for k in (term, fold(term), _singular(fold(term), set(by_key))) if k in by_key), None)
        if key is not None and by_key[key] not in out:
            out.append(by_key[key])
    return out


def value_in(parsed: dict[str, Any], result: Optional[dict[str, Any]]) -> str:
    """One answer's parsed value for the Excel report: the entries the cloud counted it towards."""
    entries = _entries(parsed.get("terms") or [], result or {})
    return ", ".join(w["text"] for w in entries) if entries else value(parsed)


def _words(text: str) -> set[str]:
    """The folded words of a text, filler and emoticons dropped."""
    return {fold(t) for t in _tokens(EMOTICON.sub(" ", text))}


def counted(text: str, parsed: Optional[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    """Which cloud entries one answer counts towards, and whether the answer is there whole.

    ``whole``: every word the answer was typed with (filler aside) is in its
    entries, and each entry is the whole answer or one whole idea of it
    (``split_ideas``), not a fragment of a sentence. The offline replay reports it.
    """
    terms = (parsed or {}).get("terms") or []
    entries = [w["key"] for w in _entries(terms, result)]
    ideas = len(terms) == 1 or bool((parsed or {}).get("ideas"))
    typed = _words(text) - (CONJUNCTIONS if len(terms) > 1 else frozenset())
    whole = ideas and len(entries) == len(terms) and typed <= set().union(*(_words(t) for t in terms))
    return {"entries": entries, "whole": whole}
