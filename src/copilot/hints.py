"""Hints: what the model reads, the strict JSON it must answer with, and the chain of models asked.

The prompt is a stable prefix — the session's title, the item on stage and the next one with their
notes, and the clock state computed from the live clocks (drift is measured, never guessed) — then
the last minutes of the transcript verbatim. The answer is JSON: at most three hints of at most
twelve words, each typed ``next_q`` (a question to ask next), ``notice`` (something said worth
following up) or ``recap`` (a point worth summing up). No hint is a fine answer. A ``drift`` hint
(the agenda is slipping) never comes from a model: the copilot computes it from the same clocks
(``drift_now``) and shows it itself. An answer that is not that JSON counts as a failure of the model that gave it, and
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
MODEL_HINT_TYPES = ("next_q", "notice", "recap")  # drift is measured from the clocks, never asked of a model
MAX_HINTS = 3
MAX_WORDS = 12
NOTES_CHARS = 1500  # one item's notes in the prompt: enough for real speaker notes, never a whole script
LANGUAGE_NAMES = {"en": "English", "es": "Spanish"}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
# Whisper's own words for "nothing here" ("[BLANK_AUDIO]", "(music)") are not speech.
NOT_SPEECH = re.compile(r"^[\[(][^\])]*[\])]$|^[\s.…,!?-]*$")


class HintFormatError(Exception):
    """The model's answer is not in the shape asked for (the hints JSON, a non-empty summary)."""


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
    text: str = ""  # a text answer (the rolling summary) instead of hints
    failure: str = ""  # "" | "hub_down" | "timeout" | "error"
    detail: str = ""


def _section_drift(run: dict[str, Any], cur: Optional[dict[str, Any]], started_at: int,
                   section_entered: dict[str, int], now_ms: int) -> Optional[tuple[dict[str, Any], float, int]]:
    """The section on stage, the minutes spent in it and how many minutes behind the plan it runs
    (the same arithmetic as ``live.js``'s ``timing``); None outside any section."""
    section = next((s for s in run.get("sections") or [] if cur and s["id"] == cur.get("section_id")), None)
    if not section:
        return None
    entered = section_entered.get(section["id"], now_ms)
    in_section = (now_ms - entered) / 60000
    drift = round((entered - started_at) / 60000 - section["planned_start"] + max(0.0, in_section - section["minutes"]))
    return section, in_section, drift


def clock_state(run: dict[str, Any], cur: Optional[dict[str, Any]], started_at: Optional[int],
                section_entered: dict[str, int], duration_minutes: int, now_ms: int) -> str:
    """The presenter's clocks in one sentence."""
    if not started_at:
        return "The session clock has not been started."
    elapsed = (now_ms - started_at) / 60000
    parts = [f"Session clock: {elapsed:.0f} of {duration_minutes} min."]
    measured = _section_drift(run, cur, started_at, section_entered, now_ms)
    if measured:
        section, in_section, drift = measured
        parts.append(f"Section \"{section['name']}\": {in_section:.0f} of {section['minutes']} min planned.")
        parts.append("On time." if drift == 0 else f"{drift} min behind the plan." if drift > 0 else f"{-drift} min ahead of the plan.")
    return " ".join(parts)


def drift_now(run: dict[str, Any], cur: Optional[dict[str, Any]], started_at: Optional[int],
              section_entered: dict[str, int], duration_minutes: int, now_ms: int) -> Optional[tuple[str, str, int]]:
    """How far behind the plan the session runs, as ``(key, name, minutes)``: the section on stage
    (keyed by its id), or outside any section the whole session past its length (keyed
    ``session``). None while the clock is not started or nothing runs behind."""
    if not started_at:
        return None
    measured = _section_drift(run, cur, started_at, section_entered, now_ms)
    if measured:
        section, _, drift = measured
        return (section["id"], str(section["name"]), drift) if drift > 0 else None
    over = round((now_ms - started_at) / 60000 - duration_minutes)
    return ("session", "", over) if duration_minutes and over > 0 else None


DRIFT_TEXT = {"en": ("{m} min behind the plan", "{m} min behind the plan in {name}"),
              "es": ("{m} min de retraso sobre el plan", "{m} min de retraso en {name}")}


def drift_hint(language: str, name: str, minutes: int) -> Hint:
    """The computed ``drift`` hint, in the session's language, cut to the hint length."""
    plain, named = DRIFT_TEXT.get(language, DRIFT_TEXT["en"])
    words = (named.format(m=minutes, name=name) if name else plain.format(m=minutes)).split()
    return Hint("drift", " ".join(words[:MAX_WORDS]))


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


def speaker_labels(mode: str, interviewee: str) -> dict[str, str]:
    """How each channel is named to the model: in an interview ``them`` is the interviewee, by name when known."""
    return {"me": "me", "them": interviewee or "interviewee"} if mode == "interview" else {"me": "me", "them": "them"}


def interviewee_clause(interviewee: str) -> str:
    """Who ``them`` is in an interview's transcript, in the words the prompts use."""
    return f"'{interviewee}' is the interviewee" if interviewee else "'interviewee' is the person being interviewed"


def system_prompt(language: str, mode: str = "workshop", interviewee: str = "") -> str:
    lang = LANGUAGE_NAMES.get(language, "English")
    if mode == "interview":
        return (
            "You are a discreet copilot for an interviewer running a remote one-to-one interview. You read the "
            "interview plan and the latest transcript and suggest at most three very short hints the interviewer "
            f"can glance at while listening. In the transcript, 'me' is the interviewer and {interviewee_clause(interviewee)}.\n"
            'Answer with JSON only, no other text: {"hints": [{"type": "next_q", "text": "..."}]}.\n'
            "Types: next_q = a follow-up question to ask next, built on what the interviewee just said; "
            "notice = a question the interviewee has not answered yet, or something they said worth digging into; "
            "recap = a point worth summing up now, rarely needed in an interview. Favour next_q and notice. "
            "The app warns about time itself, so give no time warnings. "
            f"Each text has at most {MAX_WORDS} words and is written in {lang}.\n"
            'Prefer no hint to a weak one: {"hints": []}.'
        )
    return (
        "You are a discreet copilot for a facilitator running a live session (a workshop, an interview or a "
        "meeting). You read the session plan and the latest transcript and suggest at most three very short "
        "hints the facilitator can glance at while talking. In the transcript, 'me' is the facilitator and "
        "'them' is everyone else on the call.\n"
        'Answer with JSON only, no other text: {"hints": [{"type": "next_q", "text": "..."}]}.\n'
        "Types: next_q = a question to ask next; notice = something said that is worth following up; "
        "recap = a point worth summing up now. The app warns about time itself, so give no time warnings. "
        f"Each text has at most {MAX_WORDS} words and is written in {lang}.\n"
        'Prefer no hint to a weak one: {"hints": []}.'
    )


def transcript_text(lines: Sequence[Line], now: float, labels: Optional[dict[str, str]] = None) -> str:
    """The lines, oldest first, each stamped with how long before ``now`` its audio ended and named by
    ``labels`` (a speaker missing from it keeps its channel name)."""
    names = labels or {}

    def stamp(line: Line) -> str:
        ago = max(0, int(now - line.ended_at))
        return f"[-{ago // 60:02d}:{ago % 60:02d}]"

    return "\n".join(f"{stamp(ln)} {names.get(ln.speaker, ln.speaker)}: {ln.text}" for ln in lines) or "(nothing said yet)"


def user_prompt(title: str, cur: Optional[dict[str, Any]], nxt: Optional[dict[str, Any]], clock: str,
                lines: Sequence[Line], now: float, summary: str = "", labels: Optional[dict[str, str]] = None) -> str:
    so_far = f"The conversation so far, summarised: {summary}\n" if summary else ""
    return (f"Session: {title}\n{so_far}{_item_block('On stage now', cur)}\n{_item_block('Next', nxt)}\n"
            f"Clock: {clock}\n\nTranscript, oldest first (time before now):\n{transcript_text(lines, now, labels)}")


def parse_hints(text: str, types: Sequence[str] = HINT_TYPES) -> list[Hint]:
    """The hints in a model's answer: at most three of ``types``, each cut to twelve words. Raises
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
        if not isinstance(raw, dict) or raw.get("type") not in types or not isinstance(raw.get("text"), str):
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


def model_chain(hint_models: Sequence[str], local_model: str, local_only: bool) -> list[str]:
    """The models asked, in order: the hint models then the local one, or the local one alone for a local-only session."""
    chain = [local_model] if local_only else [*hint_models, local_model]
    return list(dict.fromkeys(str(m).strip() for m in chain if str(m).strip()))


def ask_chain(complete: Complete, models: Sequence[str], messages: list[dict[str, str]], budget_s: float,
              clock: Callable[[], float] = time.monotonic,
              parse: Callable[[str], list[Hint] | str] = parse_hints) -> Answer:
    """Ask each model in turn within one time budget. Every model sits behind the same hub, so a hub
    that does not answer at all ends the chain at once. ``parse`` turns an answer into hints or a
    text, and raises ``HintFormatError`` when it is neither."""
    deadline = clock() + budget_s
    answer = Answer(failure="error", detail="no model is configured")
    for model in models:
        left = deadline - clock()
        if left <= 0.5:
            break
        try:
            out = parse(complete(model, messages, left))
            return Answer(out, model) if isinstance(out, list) else Answer(model=model, text=out)
        except HubDown as exc:
            return Answer(failure="hub_down", detail=str(exc))
        except HubTimeout as exc:
            answer = Answer(failure="timeout", detail=f"{model}: {exc}")
        except (HubFailure, HintFormatError) as exc:
            answer = Answer(failure="error", detail=f"{model}: {exc}")
    if answer.failure == "error" and answer.detail == "no model is configured" and models:
        answer = Answer(failure="timeout", detail=f"no answer within {budget_s:.0f} s")
    return answer
