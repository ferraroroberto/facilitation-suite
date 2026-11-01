"""When the overlay shows what, and how visible it is at each moment — no window, no clock of its own.

The overlay shows nothing until a hint arrives. Then it pops up (fades in), holds for
``show_seconds`` and fades out to nothing. In ``cue`` mode the pop-up is the small icon, pulsing
gently while it holds, and the words wait on the presenter's notes. New hints restart the pop-up;
hints the server clears (a new item, the time-to-live) or Copilot switched off fade it out at once,
so a stale hint is never left on screen. A style or screen change redraws what is showing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

FADE_IN_S = 0.25
FADE_OUT_S = 0.6
PULSE_S = 1.4  # one breath of the cue icon
PULSE_LOW = 0.45  # the cue's dimmest point, as a fraction of full


SAMPLE_FRESH_MS = 15000  # a sample asked for longer ago than this (an overlay started late) is not shown


@dataclass
class Popup:
    hints: list[dict[str, str]]
    mode: str
    started: float
    hold_s: float
    fade_from: Optional[float] = None  # when the fade-out began
    sample: bool = False  # Settings' sample: shown with Copilot off too


@dataclass
class Behaviour:
    """Fed snapshots and samples; asked for the alpha (0–255) to draw with at a moment."""

    popup: Optional[Popup] = None
    style: dict[str, Any] = field(default_factory=dict)
    style_rev: int = 0
    _seen: Any = None
    _sample_seen: Any = None

    def on_state(self, copilot: dict[str, Any], now: float, server_now: int, sample: list[dict[str, str]]) -> None:
        style = dict(copilot.get("overlay") or {})
        style.pop("running", None)
        if style != self.style:
            self.style, self.style_rev = style, self.style_rev + 1
        sample_at = copilot.get("sample_at")
        if sample_at and sample_at != self._sample_seen:
            self._sample_seen = sample_at
            if server_now - sample_at < SAMPLE_FRESH_MS:
                self.show(sample, now, sample=True)
        hints = copilot.get("hints") or []
        key = (copilot.get("hints_at"), tuple((h.get("type"), h.get("text")) for h in hints))
        if not copilot.get("on") or not hints:
            self._seen = key
            if self.popup is not None and not self.popup.sample:
                self.fade_out(now)
            return
        if key != self._seen:
            self._seen = key
            self.show(hints, now)

    def show(self, hints: list[dict[str, str]], now: float, *, sample: bool = False) -> None:
        if not self.style.get("enabled", True):
            return
        mode = "cue" if self.style.get("mode") == "cue" else "full"
        hold = max(2.0, float(self.style.get("show_seconds") or 12))
        self.popup = Popup(hints, mode, now, hold, sample=sample)

    def fade_out(self, now: float) -> None:
        if self.popup is not None and self.popup.fade_from is None:
            self.popup.fade_from = now

    def alpha(self, now: float) -> int:
        """0 when nothing is to be seen (the popup is then over)."""
        p = self.popup
        if p is None:
            return 0
        t = now - p.started
        fade_from = p.fade_from if p.fade_from is not None else (p.started + FADE_IN_S + p.hold_s)
        if now >= fade_from:
            left = max(0.0, 1.0 - (now - fade_from) / FADE_OUT_S)
            value = round(255 * left * min(1.0, t / FADE_IN_S) * self._pulse(p, fade_from - p.started))
            if value <= 0:
                self.popup = None  # faded to nothing: the pop-up is over
            return value
        level = min(1.0, t / FADE_IN_S) * self._pulse(p, t)
        return max(1, round(255 * level))

    @staticmethod
    def _pulse(p: Popup, t: float) -> float:
        if p.mode != "cue" or t < FADE_IN_S:
            return 1.0
        phase = (t - FADE_IN_S) / PULSE_S * 2 * math.pi
        return PULSE_LOW + (1 - PULSE_LOW) * (0.5 + 0.5 * math.cos(phase))

    def animating(self) -> bool:
        """True while something is on screen: its alpha changes by itself (a fade, a pulse, the end
        of the hold), so the loop ticks fast; otherwise it only waits for the server."""
        return self.popup is not None
