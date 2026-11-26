"""The phone control of each activity a phone in the room can answer, and the answer it sends.

A phone answers with one control, and the server turns what it sends into the
message text the activity's own parser reads, so a phone answer and a chat line
are counted the same way:

| Type | Control | The phone shows | Message text |
|---|---|---|---|
| ``word_cloud`` | ``text`` | a text box (80 characters) | the text |
| ``cards``, ``feed`` | ``text`` | a text box (280 characters) | the text |
| ``scale`` | ``scale`` | a button per number, its keyword under it | ``"4"`` |
| ``nps`` | ``scale`` | a button per number, 0 to 10 | ``"9"`` |
| ``stars`` | ``stars`` | five star buttons | ``"4"`` |
| ``choice`` | ``choice`` | a lettered button per choice (several and Send when the item allows more than one) | ``"A"``, ``"B,D"`` |
| ``number`` | ``number`` | a number box with the unit, in the item's range | ``"42"``, ``"3.5"`` |

Any other item shows the phone a "watch the screen" view.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from src.activities.answers import clean_choice_keys
from src.activities.registry import options_with_defaults, parser
from src.errors import DomainError

PHONE_CONTROLS = {
    "word_cloud": "text", "cards": "text", "feed": "text",
    "scale": "scale", "nps": "scale", "stars": "stars", "choice": "choice", "number": "number",
}
MAX_TEXT = {"word_cloud": 80}  # a word cloud entry is a word or a phrase, never a sentence
MAX_SENTENCE = 280  # a card or a feed bubble: one sentence or two
FIXED_RANGES = {"nps": (0, 10), "stars": (1, 5)}


class PollError(DomainError):
    """A phone's poll answer that cannot be counted as sent."""


def control_of(item: Optional[dict[str, Any]]) -> Optional[str]:
    """The phone control of a run item (see the table above), ``None`` for one phones cannot answer."""
    if not item or item.get("kind") != "activity" or not item.get("capture"):
        return None
    return PHONE_CONTROLS.get(str(item.get("type") or ""))


def _options(item: dict[str, Any]) -> dict[str, Any]:
    return options_with_defaults(str(item.get("type") or ""), item.get("options") or {})


def number_range(item: dict[str, Any]) -> tuple[int, int]:
    """The lowest and highest button of a ``scale``/``stars`` control, as the type's parser reads them."""
    kind = str(item.get("type") or "")
    if kind in FIXED_RANGES:
        return FIXED_RANGES[kind]
    opts = _options(item)
    try:
        lo, hi = int(opts.get("min", 1)), int(opts.get("max", 5))
    except (TypeError, ValueError):
        lo, hi = 1, 5
    return (lo, hi) if lo <= hi else (hi, lo)


def _bounds(item: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    mod = parser("number")
    opts = _options(item)
    return (mod.bound(opts, "min"), mod.bound(opts, "max")) if mod else (None, None)


def _choices(item: dict[str, Any]) -> list[str]:
    mod = parser("choice")
    return mod.choices(_options(item)) if mod else []


def fields(item: dict[str, Any]) -> dict[str, Any]:
    """What the phone needs to draw the item's control, beside its ``control``."""
    control = control_of(item)
    if control in ("scale", "stars"):
        lo, hi = number_range(item)
        words = [w.strip() for w in str(_options(item).get("keywords") or "").split(",") if w.strip()]
        return {"min": lo, "max": hi, "labels": words if len(words) == hi - lo + 1 else []}
    if control == "choice":
        labels = _choices(item)
        return {"choices": [{"key": chr(ord("A") + i), "label": c} for i, c in enumerate(labels)],
                "multiple": bool(_options(item).get("multiple"))}
    if control == "number":
        lo, hi = _bounds(item)
        return {"min": lo, "max": hi, "unit": str(_options(item).get("unit") or "")}
    return {"max_length": MAX_TEXT.get(str(item.get("type") or ""), MAX_SENTENCE)}


def _whole(item: dict[str, Any], value: Any) -> str:
    lo, hi = number_range(item)
    try:
        n = int(str(value).strip())
    except ValueError as exc:
        raise PollError(422, "bad_value", f"pick a number from {lo} to {hi}") from exc
    if not lo <= n <= hi:
        raise PollError(422, "bad_value", f"pick a number from {lo} to {hi}")
    return str(n)


def _picked(item: dict[str, Any], value: Any) -> str:
    try:
        return clean_choice_keys(value, len(_choices(item)), bool(_options(item).get("multiple")))
    except ValueError as exc:
        raise PollError(422, "bad_value", str(exc)) from exc


def _number(item: dict[str, Any], value: Any) -> str:
    try:
        n = float(str(value).strip().replace(",", "."))
    except ValueError as exc:
        raise PollError(422, "bad_value", "type a number") from exc
    lo, hi = _bounds(item)
    if not math.isfinite(n) or (lo is not None and n < lo) or (hi is not None and n > hi):
        raise PollError(422, "bad_value", "the number is out of range")
    mod = parser("number")
    return mod.shown(n) if mod else str(n)


def clean_value(item: dict[str, Any], value: Any) -> str:
    """The answer as the message text the item's parser reads; ``PollError`` when it is not one."""
    control = control_of(item)
    if control in ("scale", "stars"):
        return _whole(item, value)
    if control == "choice":
        return _picked(item, value)
    if control == "number":
        return _number(item, value)
    limit = MAX_TEXT.get(str(item.get("type") or ""), MAX_SENTENCE)
    text = " ".join(str(value or "").split())[:limit].strip()
    if not text:
        raise PollError(422, "bad_value", "the answer is empty")
    return text
