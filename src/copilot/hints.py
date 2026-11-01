"""Hints: what the model reads, the strict JSON it must answer with, and the chain of models asked.

The prompt is a stable prefix — the session's title, the item on stage and the next one with their
notes, and the clock state computed from the live clocks (drift is measured, never guessed) — then
the last minutes of the transcript verbatim. The answer is JSON: at most three hints of at most
twelve words, each typed ``next_q`` (a question to ask next), ``notice`` (something said worth
following up), ``drift`` (the agenda is slipping) or ``recap`` (a point worth summing up). No hint is
a fine answer. An answer that is not that JSON counts as a failure of the model that gave it, and
the next model in the chain is asked within what is left of the time budget.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from src.copilot.hub_client import HubDown, HubFailure, HubTimeout

HINT_TYPES = ("next_q", "notice", "drift", "recap")
MAX_HINTS = 3
MAX_WORDS = 12
NOTES_CHARS = 1500  # one item's notes in the prompt: enough for real speaker notes, never a whole script
LANGUAGE_NAMES = {"en": "English", "es": "Spanish"}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class HintFormatError(Exception):
    """The model's answer is not the hints JSON."""


@dataclass(frozen=True)
class Hint:
    type: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class Line:
    """One transcript line: ``ended_at`` is the wall clock (epoch s) its audio ended."""

    speaker: str
    text: str
    ended_at: float


@dataclass
class Answer:
    """What the chain produced: the hints and the model that gave them, or why nobody did."""

    hints: list[Hint] = field(default_factory=list)
    model: str = ""
    failure: str = ""  # "" | "hub_down" | "timeout" | "error"
    detail: str = ""


def clock_state(run: dict[str, Any], cur: Optional[dict[str, Any]], started_at: Optional[int],
                section_entered: dict[str, int], duration_minutes: int, now_ms: int) -> str:
    """The presenter's clocks in one sentence (the same arithmetic as ``live.js``'s ``timing``)."""
    if not started_at:
        return "The session clock has not been started."
    elapsed = (now_ms - started_at) / 60000
    parts = [f"Session clock: {elapsed:.0f} of {duration_minutes} min."]
    section = next((s for s in run.get("sections") or [] if cur and s["id"] == cur.get("section_id")), None)
    if section:
        entered = section_entered.get(section["id"], now_ms)
        in_section = (now_ms - entered) / 60000
        drift = round((entered - started_at) / 60000 - section["planned_start"] + max(0.0, in_section - section["minutes"]))
        parts.append(f"Section \"{section['name']}\": {in_section:.0f} of {section['minutes']} min planned.")
        parts.append("On time." if drift == 0 else f"{drift} min behind the plan." if drift > 0 else f"{-drift} min ahead of the plan.")
    return " ".join(parts)


def _item_block(label: str, item: Optional[dict[str, Any]]) -> str:
    if not item:
        return f"{label}: nothing (the end of the session)."
    lines = [f"{label}: {item.get('title') or 'untitled'} ({item.get('type_label') or item.get('kind')})"]
    if item.get("question"):
        lines.append(f"  Question: {item['question']}")
    notes = str(item.get("notes") or "").strip()
    if notes:
        lines.append("  Notes: " + notes[:NOTES_CHARS])
    return "\n".join(lines)


def system_prompt(language: str) -> str:
    lang = LANGUAGE_NAMES.get(language, "English")
    return (
        "You are a discreet copilot for a facilitator running a live session (a workshop, an interview or a "
        "meeting). You read the session plan and the latest transcript and suggest at most three very short "
        "hints the facilitator can glance at while talking. In the transcript, 'me' is the facilitator and "
        "'them' is everyone else on the call.\n"
        'Answer with JSON only, no other text: {"hints": [{"type": "next_q", "text": "..."}]}.\n'
        "Types: next_q = a question to ask next; notice = something said that is worth following up; "
        "drift = the agenda is slipping (use the clock state given, never guess); recap = a point worth "
        f"summing up now. Each text has at most {MAX_WORDS} words and is written in {lang}.\n"
        'Prefer no hint to a weak one: {"hints": []}.'
    )


def user_prompt(title: str, cur: Optional[dict[str, Any]], nxt: Optional[dict[str, Any]], clock: str,
                lines: Sequence[Line], now: float) -> str:
    def stamp(line: Line) -> str:
        ago = max(0, int(now - line.ended_at))
        return f"[-{ago // 60:02d}:{ago % 60:02d}]"

    transcript = "\n".join(f"{stamp(ln)} {ln.speaker}: {ln.text}" for ln in lines) or "(nothing said yet)"
    return (f"Session: {title}\n{_item_block('On stage now', cur)}\n{_item_block('Next', nxt)}\n"
            f"Clock: {clock}\n\nTranscript, oldest first (time before now):\n{transcript}")


def parse_hints(text: str) -> list[Hint]:
    """The hints in a model's answer: at most three, each cut to twelve words. Raises
    ``HintFormatError`` when the answer is not ``{"hints": [...]}``."""
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise HintFormatError("no JSON object in the answer")
    try:
        data = json.loads(body[start:end + 1])
    except ValueError as exc:
        raise HintFormatError(f"the answer is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hints"), list):
        raise HintFormatError('the answer has no "hints" list')
    out: list[Hint] = []
    for raw in data["hints"]:
        if not isinstance(raw, dict) or raw.get("type") not in HINT_TYPES or not isinstance(raw.get("text"), str):
            continue
        words = raw["text"].split()
        if not words:
            continue
        text_out = " ".join(words[:MAX_WORDS])
        if len(words) > MAX_WORDS:
            text_out = text_out.rstrip(".,;:!?…") + "…"
        out.append(Hint(raw["type"], text_out))
        if len(out) == MAX_HINTS:
            break
    return out


Complete = Callable[[str, list[dict[str, str]], float], str]


def ask_chain(complete: Complete, models: Sequence[str], messages: list[dict[str, str]], budget_s: float,
              clock: Callable[[], float] = time.monotonic) -> Answer:
    """Ask each model in turn within one time budget. Every model sits behind the same hub, so a hub
    that does not answer at all ends the chain at once."""
    deadline = clock() + budget_s
    answer = Answer(failure="error", detail="no model is configured")
    for model in models:
        left = deadline - clock()
        if left <= 0.5:
            break
        try:
            return Answer(parse_hints(complete(model, messages, left)), model)
        except HubDown as exc:
            return Answer(failure="hub_down", detail=str(exc))
        except HubTimeout as exc:
            answer = Answer(failure="timeout", detail=f"{model}: {exc}")
        except (HubFailure, HintFormatError) as exc:
            answer = Answer(failure="error", detail=f"{model}: {exc}")
    if answer.failure == "error" and answer.detail == "no model is configured" and models:
        answer = Answer(failure="timeout", detail=f"no answer within {budget_s:.0f} s")
    return answer
