"""Live actions — the one list of intents every control surface sends.

Keyboard (stage and presenter), presenter buttons, the phone remote and the
Stream Deck all send these ids; nothing else mutates the live state. Adding an
action is one entry here (epic §12: same shape as home-automation's action
registry). ``arg`` actions take one path segment (``goto_section/3``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from src.live.hub import LiveError, LiveHub


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    run: Callable[[LiveHub, Optional[str]], None]
    arg: Optional[str] = None  # the argument's name when the action takes one
    stream_deck: bool = True


def _int(arg: Optional[str], name: str) -> int:
    try:
        return int(str(arg))
    except (TypeError, ValueError) as exc:
        raise LiveError(422, "bad_argument", f"{name} must be a number") from exc


ACTIONS: dict[str, Action] = {a.id: a for a in (
    Action("next", "Next item", lambda h, a: h.next()),
    Action("prev", "Previous item", lambda h, a: h.prev()),
    Action("blackout", "Blackout on/off", lambda h, a: h.toggle_blackout()),
    Action("names_toggle", "Names on stage on/off", lambda h, a: h.toggle_names()),
    Action("timer_toggle", "Timer start/pause", lambda h, a: h.timer_toggle()),
    Action("timer_add_minute", "Timer +1 min", lambda h, a: h.timer_add_minute()),
    Action("timer_reset", "Timer reset", lambda h, a: h.timer_reset()),
    Action("goto_section", "Go to section", lambda h, a: h.goto_section(_int(a, "section")), arg="n"),
    Action("goto", "Go to item", lambda h, a: h.goto(_int(a, "item") - 1), arg="n", stream_deck=False),
    Action("clock_start", "Start the session clock", lambda h, a: h.clock_start(), stream_deck=False),
    Action("clock_reset", "Reset the session clock", lambda h, a: h.clock_reset(), stream_deck=False),
)}


def register(action: Action) -> None:
    """Later services (capture, OBS) add their actions at startup."""
    ACTIONS[action.id] = action


def run_action(hub: LiveHub, action_id: str, arg: Optional[str] = None) -> dict[str, Any]:
    action = ACTIONS.get(action_id)
    if action is None:
        raise LiveError(404, "unknown_action", f"No action {action_id!r}")
    if action.arg and arg is None:
        raise LiveError(422, "missing_argument", f"{action_id} needs /{action.arg}")
    if hub.session_id is None:
        raise LiveError(409, "not_live", "No session is live — open the presenter first")
    action.run(hub, arg)
    return {"ok": True, "action": action_id}


def catalog() -> list[dict[str, Any]]:
    return [{"id": a.id, "label": a.label, "arg": a.arg, "stream_deck": a.stream_deck} for a in ACTIONS.values()]
