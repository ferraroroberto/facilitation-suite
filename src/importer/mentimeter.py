"""A slide titled ``mentimeter …`` → the poll activity of the closest type.

The deck keeps a Mentimeter slide as a placeholder: its title names it
(``Mentimeter – word cloud – What is one word for today?``) and its other text
boxes may hold the question and the choices. The type is named in the title
or the slide text when it is there, else guessed: two or more choices → a
choice poll, a range like "1–5" → a scale, "recommend" → NPS, anything else →
a word cloud. A slide with no question anywhere stays a skipped slide, with
the reason.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Optional

TITLE_RE = re.compile(r"^\s*mentimeter\b(.*)$", re.I | re.S)
_SEP = re.compile(r"^\s*[\-–—:|]\s*")
# Mentimeter's own slide-type names, longest first so "multiple choice" wins over "choice".
_TYPE_WORDS: list[tuple[str, str]] = [
    (r"word\s*cloud", "word_cloud"),
    (r"multiple\s*choice", "choice"),
    (r"open[\s\-]*ended", "cards"),
    (r"q\s*&\s*a", "cards"),
    (r"net\s*promoter|nps", "nps"),
    (r"rating|stars?", "stars"),
    (r"scales?", "scale"),
    (r"numbers?", "number"),
    (r"choices?|poll", "choice"),
    (r"open", "cards"),
]
_TYPE_RE = [(re.compile(rf"^(?:{words})\b", re.I), kind) for words, kind in _TYPE_WORDS]
_RANGE_RE = re.compile(r"\b(\d{1,2})\s*(?:-|–|—|to|a|al)\s*(\d{1,2})\b", re.I)
_RECOMMEND_RE = re.compile(r"\b(recommend|recomendar[ií]as?)\b", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[•\-–*·▪]|[A-Za-z][).]|\d{1,2}[).])\s+")
CHOICE_MAX_CHARS = 60  # longer lines are prose on the slide, not a choice
UNREADABLE = "Mentimeter slide with no readable question"
# What a title says when it names the slide but not the question: "Mentimeter slide", "Mentimeter 2".
_NO_QUESTION = re.compile(r"^(?:slide|diapositiva|placeholder|here|aqu[ií])?\s*\d*$", re.I)
_LEAD_NUMBER = re.compile(r"^\s*\d+(?=\s*[\-–—:|]|\s*$)")


def _clean(text: str) -> str:
    return " ".join(text.split())


def _split_type(text: str) -> tuple[Optional[str], str]:
    """``word cloud – What…`` → (``word_cloud``, ``What…``); no type word → (None, text)."""
    text = _SEP.sub("", text, count=1)
    for pattern, kind in _TYPE_RE:
        m = pattern.match(text)
        if m:
            rest = text[m.end():]
            # The type word must stand alone ("Scale – How…"), not open the question ("Numbers matter?").
            if not rest.strip() or _SEP.match(rest):
                return kind, _SEP.sub("", rest, count=1).strip()
    return None, text.strip()


def _lines(texts: Iterable[str], title: str) -> list[str]:
    """The slide's text lines other than its title, in order."""
    out: list[str] = []
    for block in texts:
        if _clean(block) == _clean(title):
            continue
        for line in re.split(r"[\r\n\x0b]+", block):
            line = _clean(line)
            if line:
                out.append(line)
    return out


def _choice(line: str) -> str:
    # The choice option is one comma-separated string, so a comma inside a choice becomes a slash.
    return _BULLET_RE.sub("", line).replace(",", " /").strip()


def classify_mentimeter(title: str, texts: Iterable[str] = ()) -> Optional[dict[str, Any]]:
    """A ``mentimeter …`` slide → its activity placeholder, a skip with a reason, or None for any other slide."""
    m = TITLE_RE.match(title or "")
    if not m:
        return None
    kind, question = _split_type(_LEAD_NUMBER.sub("", _clean(m.group(1))))
    if _NO_QUESTION.match(question):
        question = ""
    lines = _lines(texts, title)
    if lines and kind is None:
        named, rest = _split_type(lines[0])
        if named is not None:
            kind = named
            lines = ([rest] if rest else []) + lines[1:]
    if not question and lines:
        question, lines = lines[0], lines[1:]
    if not question:
        return {"kind": "skip", "reason": UNREADABLE}
    choices = [c for c in (_choice(line) for line in lines) if c and len(c) <= CHOICE_MAX_CHARS]
    span = _RANGE_RE.search(question)
    if kind is None:
        if len(choices) >= 2:
            kind = "choice"
        elif _RECOMMEND_RE.search(question):
            kind = "nps"
        elif span:
            kind = "scale"
        else:
            kind = "word_cloud"
    options: dict[str, Any] = {}
    if kind == "choice" and len(choices) >= 2:
        options["choices"] = ", ".join(choices)
    if kind == "scale" and span and int(span.group(1)) < int(span.group(2)):
        options.update(min=int(span.group(1)), max=int(span.group(2)))
    q = question[0].upper() + question[1:]
    return {"kind": "activity", "type": kind, "question": q, "title": q, "options": options}
