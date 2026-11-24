"""A feedback form's questions, a phone's answers to them and what they add up to — pure, no I/O.

A form is a plan item of type ``form``: its title is the item's title and its
``options`` hold the rest, written by the Plan tab's form builder:

    intro: str                 the text under the title on the phone
    show_count: bool           the stage shows how many responses came in (off by default)
    questions: [               in order, each one
      {id, type, text, required,
       choices, multiple,      choice: the choices, more than one allowed
       min, max, unit,         number: an optional range and unit
       low, high}              scale (0 to 10) and nps: the words under each end
    ]

Question types: ``text`` (free text), ``number``, ``scale`` (0 to 10),
``stars`` (1 to 5), ``nps`` (0 to 10, scored) and ``choice`` (one or several).

A **response** is one submission: ``{response_id, name, answers, at}``, where
``answers`` maps a question id to its cleaned value (a string for text and
choice keys joined by commas, a number otherwise). Nothing here ever reaches
the stage or a phone other than the one that sent it.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Any, Optional

from src.errors import DomainError

FORM_TYPE = "form"
QUESTION_TYPES = ("text", "number", "scale", "stars", "nps", "choice")
MAX_QUESTIONS = 40
MAX_TEXT = 1000  # characters in one free-text answer: a paragraph, never an essay
MAX_NAME = 40
RANGES = {"scale": (0, 10), "stars": (1, 5), "nps": (0, 10)}
NPS_GROUPS = (("promoters", 9, 10), ("passives", 7, 8), ("detractors", 0, 6))


class FormError(DomainError):
    """A form that cannot be used as asked (closed, unknown, an answer it cannot take)."""


def _num(value: Any) -> Optional[float]:
    try:
        n = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _shown(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def choices_of(q: dict[str, Any]) -> list[str]:
    raw = q.get("choices") or []
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return [c for c in (" ".join(str(x).split()) for x in items) if c]


def questions(options: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """The form's questions, each with a known type, an id and its own fields only."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate((options or {}).get("questions") or []):
        if not isinstance(raw, dict) or len(out) >= MAX_QUESTIONS:
            continue
        kind = str(raw.get("type") or "text")
        if kind not in QUESTION_TYPES:
            continue
        qid = str(raw.get("id") or f"q{i + 1}")[:40]
        while qid in seen:
            qid += "x"
        seen.add(qid)
        q: dict[str, Any] = {"id": qid, "type": kind, "text": str(raw.get("text") or "").strip(),
                             "required": bool(raw.get("required"))}
        if kind == "choice":
            q.update(choices=choices_of(raw), multiple=bool(raw.get("multiple")))
        elif kind == "number":
            q.update(min=_num(raw.get("min")) if raw.get("min") not in (None, "") else None,
                     max=_num(raw.get("max")) if raw.get("max") not in (None, "") else None,
                     unit=str(raw.get("unit") or ""))
        elif kind in ("scale", "nps"):
            q.update(low=str(raw.get("low") or ""), high=str(raw.get("high") or ""))
        if kind in RANGES:
            q["min"], q["max"] = RANGES[kind]
        out.append(q)
    return out


def public_form(item: dict[str, Any]) -> dict[str, Any]:
    """What the phone draws: the title, the intro and the questions (no answers, no count)."""
    opts = item.get("options") or {}
    return {"title": str(item.get("title") or "").strip(), "intro": str(opts.get("intro") or ""),
            "questions": questions(opts)}


def clean_name(name: Any) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME].strip()


def _clean_one(q: dict[str, Any], value: Any) -> Any:
    """One answer as stored; ``ValueError`` with the phone's message when it cannot be."""
    kind = q["type"]
    if kind == "text":
        # Line breaks stay (a paragraph of feedback), runs of spaces inside a line do not.
        return "\n".join(" ".join(line.split()) for line in str(value).splitlines()).strip()[:MAX_TEXT]
    if kind == "choice":
        keys = [chr(ord("A") + i) for i in range(len(q["choices"]))]
        raw = value if isinstance(value, list) else str(value).split(",")
        picked = sorted(dict.fromkeys(str(k).strip().upper() for k in raw if str(k).strip()))
        if any(k not in keys for k in picked):
            raise ValueError("pick one of the choices")
        if len(picked) > 1 and not q["multiple"]:
            raise ValueError("pick one choice only")
        return ",".join(picked)
    n = _num(value)
    if n is None:
        raise ValueError("type a number")
    lo, hi = q.get("min"), q.get("max")
    if kind != "number" and not float(n).is_integer():
        raise ValueError(f"pick a whole number from {lo} to {hi}")
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ValueError("the number is out of range")
    return int(n) if float(n).is_integer() else n


def clean_answers(qs: list[dict[str, Any]], answers: Any) -> dict[str, Any]:
    """The answers a phone sent, cleaned; ``FormError`` 422 ``bad_answers`` naming each question that failed."""
    given = answers if isinstance(answers, dict) else {}
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for q in qs:
        raw = given.get(q["id"])
        empty = raw is None or (isinstance(raw, list) and not raw) or (not isinstance(raw, list) and not str(raw).strip())
        if empty:
            if q["required"]:
                errors[q["id"]] = "this question needs an answer"
            continue
        try:
            value = _clean_one(q, raw)
        except ValueError as exc:
            errors[q["id"]] = str(exc)
            continue
        if value == "" and q["required"]:
            errors[q["id"]] = "this question needs an answer"
        elif value != "":
            out[q["id"]] = value
    if errors:
        raise FormError(422, "bad_answers", "Some answers need a look", {"errors": errors})
    if not out:
        raise FormError(422, "bad_answers", "The form is empty — answer at least one question", {"errors": {}})
    return out


def _labels(q: dict[str, Any], value: Any) -> str:
    """A choice answer's keys as the choices' words ("Office; Remote")."""
    words = q.get("choices") or []
    keys = [k for k in str(value).split(",") if k]
    return "; ".join(words[ord(k) - ord("A")] if 0 <= ord(k) - ord("A") < len(words) else k for k in keys)


def _average(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def summary(q: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    """One question's aggregate over every response that answered it."""
    given = [(r, r["answers"][q["id"]]) for r in responses if q["id"] in (r.get("answers") or {})]
    out: dict[str, Any] = {"id": q["id"], "type": q["type"], "text": q["text"], "count": len(given)}
    kind = q["type"]
    if kind == "text":
        out["answers"] = [{"name": r.get("name") or "", "text": str(v), "at": r.get("at")} for r, v in given]
    elif kind == "choice":
        tally = {chr(ord("A") + i): 0 for i in range(len(q["choices"]))}
        for _, v in given:
            for k in str(v).split(","):
                if k in tally:
                    tally[k] += 1
        out["choices"] = [{"key": k, "label": q["choices"][i], "count": n} for i, (k, n) in enumerate(tally.items())]
    else:
        nums = [float(v) for _, v in given]
        out["average"] = _average(nums)
        if kind in RANGES:
            lo, hi = RANGES[kind]
            out["bins"] = [{"value": n, "count": sum(1 for x in nums if x == n)} for n in range(lo, hi + 1)]
        if kind == "nps":
            groups = {name: sum(1 for x in nums if a <= x <= b) for name, a, b in NPS_GROUPS}
            out.update(groups)
            out["score"] = round(100 * (groups["promoters"] - groups["detractors"]) / len(nums)) if nums else None
        if kind == "number":
            out["min"], out["max"] = (min(nums), max(nums)) if nums else (None, None)
    return out


def results(qs: list[dict[str, Any]], responses: list[dict[str, Any]]) -> dict[str, Any]:
    return {"count": len(responses), "questions": [summary(q, responses) for q in qs]}


def _when(at: Any) -> str:
    try:
        return datetime.fromtimestamp(int(at) / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def csv_text(qs: list[dict[str, Any]], responses: list[dict[str, Any]]) -> str:
    """One row per submission, one column per question (a BOM first, so Excel reads it as UTF-8)."""
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(["Submitted", "Name", *[q["text"] or q["id"] for q in qs]])
    for r in responses:
        answers = r.get("answers") or {}
        cells = []
        for q in qs:
            v = answers.get(q["id"])
            cells.append("" if v is None else _labels(q, v) if q["type"] == "choice"
                         else _shown(v) if isinstance(v, (int, float)) else str(v))
        out.writerow([_when(r.get("at")), r.get("name") or "", *cells])
    return "﻿" + buf.getvalue()
