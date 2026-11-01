"""A synthetic copilot conversation for a session that already ran (``live/events.jsonl`` written).

Writes ``live/copilot/transcript.jsonl`` and ``live/copilot/hints.jsonl`` the way the copilot writes
them, with every line and hint placed inside the window its item was on stage, so the same helper
serves the unit tests (the demo run's clock) and the e2e story (the real clock of the run it drove).
Synthetic text only: a real transcript never enters the repo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Per item, what was said and the hints, at a fraction of the item's time on stage. A fraction of 1
# lands as the next item came on: the copilot dropped that answer as stale.
SCRIPT: dict[str, list[tuple[float, dict[str, Any]]]] = {
    "act-weather": [
        (0.1, {"speaker": "me", "text": "How is everyone's inner weather this morning?"}),
        (0.3, {"speaker": "them", "text": "Cloudy with a chance of coffee."}),
        (0.4, {"reason": "item", "model": "demo-model", "hints": [{"type": "next_q", "text": "Ask who picked rain and why"}]}),
        (0.6, {"speaker": "them", "text": "Sunny, the week started well."}),
        (1.0, {"reason": "them", "model": "demo-model", "dropped": "stale",
               "hints": [{"type": "notice", "text": "Two people mention coffee"}]}),
    ],
    "act-kryptonite": [
        (0.1, {"speaker": "me", "text": "What drains your energy at work?"}),
        (0.3, {"reason": "item", "model": "demo-model", "hints": [], "failure": "timeout",
               "detail": "demo-model: no answer within 8 s"}),
        (0.5, {"speaker": "them", "text": "Meetings that could have been an email."}),
        (0.7, {"reason": "drift", "model": "clock", "hints": [{"type": "drift", "text": "Personal readme runs 5 min late"}],
               "drift_min": 5}),
    ],
}
LINE_LAG_MS = 1500
# The e2e run moves through an item in tens of ms; below this the fractions would share a millisecond.
MIN_WINDOW_MS = 10


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds")


def _windows(folder: Path) -> dict[str, tuple[int, int]]:
    """Each item's first time on stage: (came on, the next item came on), in epoch ms."""
    events = [json.loads(ln) for ln in (folder / "live" / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    moves = [(int(datetime.fromisoformat(e["at"]).timestamp() * 1000), e["item_id"])
             for e in events if e.get("event") in ("session_live", "item")]
    out: dict[str, tuple[int, int]] = {}
    for n, (at, item_id) in enumerate(moves):
        end = moves[n + 1][0] if n + 1 < len(moves) else at + 60_000
        out.setdefault(item_id, (at, end))
    return out


def write_demo_conversation(folder: Path) -> dict[str, list[dict[str, Any]]]:
    """The copilot's two logs for ``SCRIPT``; returns the records written, in file order."""
    windows = _windows(folder)
    lines: list[dict[str, Any]] = []
    hints: list[dict[str, Any]] = []
    for item_id, script in SCRIPT.items():
        start, end = windows[item_id]
        if end - start < MIN_WINDOW_MS:
            raise ValueError(f"{item_id} was on stage {end - start} ms: too short to place the conversation in order")
        for frac, rec in script:
            at = start + int((end - start) * frac)
            if "speaker" in rec:
                lines.append({"at": _iso(at + LINE_LAG_MS), "t0": 0.0, "t1": 4.0, "lag_ms": LINE_LAG_MS, **rec})
            else:
                hints.append({"at": _iso(at), "item_id": item_id, "lag_ms": 2100, **rec})
    copilot = folder / "live" / "copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    for name, records in (("transcript.jsonl", lines), ("hints.jsonl", hints)):
        (copilot / name).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return {"lines": lines, "hints": hints}
