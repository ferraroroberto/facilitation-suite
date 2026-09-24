"""Captures: which chat messages belong to which activity, and its live result.

Epic §8. **Start** opens a window on the activity; every non-hidden
participant message received while a window is open belongs to it until
**Stop**. A stopped capture can be reopened (a new window; what arrived while
it was stopped stays out). The facilitator's own messages ("You") never
count. Hidden messages are excluded everywhere and can be un-hidden.

At every Stop the result is **frozen**: ``live/captures/<item>.json`` (every
answer with its name and parsed value, plus the result) at once, and
``live/captures/<item>.png`` — the stage exactly as it looked — rendered by
a headless browser from ``/stage?freeze=<item>`` in the background.

Timers: an item timer that starts ``with_capture`` starts with it (and
pauses when the capture stops by hand); a timer that ends with
``stop_capture`` stops it.

State (windows + hidden ids) lives in ``live/captures.json`` so a restarted
server resumes mid-capture.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.activities.registry import options_with_defaults, parser, result_for
from src.chat.hub import ChatHub
from src.live.actions import Action, register
from src.live.hub import LiveError, LiveHub, now_ms
from src.no_window import NO_WINDOW
from src.sessions.store import atomic_write_text

logger = logging.getLogger(__name__)

STATE_FILE = "captures.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PUSH_EVERY_S = 0.25  # at most four result pushes a second during a burst


class CaptureService:
    def __init__(self, live: LiveHub, chat: ChatHub) -> None:
        self.live, self.chat = live, chat
        self.windows: dict[str, list[list[Optional[int]]]] = {}
        self.hidden: set[int] = set()
        # Places fixed by hand on the presenter (map): message id → GeoNames id.
        self.places: dict[int, str] = {}
        self._push_pending = False
        self._session: Optional[str] = None
        self.freeze_url: Optional[str] = None  # the server's own base URL, for the PNG
        live.extra_state.append(self.state_fields)
        live.item_listeners.append(self._on_item)
        live.timer_end_listeners.append(self._on_timer_end)
        live.session_listeners.append(lambda sid: self._load())
        chat.listeners.append(self._on_messages)
        register(Action("capture_toggle", "Capture start/stop", lambda h, a: self.toggle()))
        register(Action("hide_message", "Hide a chat message", lambda h, a: self.set_hidden(_int(a), True), arg="id", stream_deck=False))
        register(Action("unhide_message", "Show a hidden message", lambda h, a: self.set_hidden(_int(a), False), arg="id", stream_deck=False))

    # ----------------------------------------------------------- queries

    def status(self, item_id: str) -> str:
        wins = self.windows.get(item_id)
        if not wins:
            return "idle"
        return "live" if wins[-1][1] is None else "stopped"

    def active_item(self) -> Optional[str]:
        return next((k for k in self.windows if self.status(k) == "live"), None)

    def messages_for(self, item_id: str, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        wins = self.windows.get(item_id) or []
        if not wins:
            return []
        out = []
        for m in self.chat.messages:
            if m.get("own"):
                continue
            t = m["received_at"]
            inside = any(s is not None and t >= s and (e is None or t < e) for s, e in wins)  # [start, stop)
            if inside and (include_hidden or m["id"] not in self.hidden):
                out.append(dict(m, place_id=self.places[m["id"]]) if m["id"] in self.places else m)
        return out

    def result(self, item: dict[str, Any]) -> Optional[dict[str, Any]]:
        if item.get("kind") != "activity" or not item.get("type"):
            return None
        return result_for(item["type"], item.get("options") or {}, self.messages_for(item["id"]))

    def state_fields(self) -> dict[str, Any]:
        self._load()
        cur = self.live.current()
        out: dict[str, Any] = {"hidden": sorted(self.hidden), "capture": None}
        if cur and cur.get("capture"):
            counted = self.messages_for(cur["id"])
            hidden_in = [m["id"] for m in self.messages_for(cur["id"], include_hidden=True) if m["id"] in self.hidden]
            out["capture"] = {
                "item_id": cur["id"], "status": self.status(cur["id"]), "windows": self.windows.get(cur["id"], []),
                "answers": len(counted), "people": len({m["sender"] for m in counted}), "hidden": len(hidden_in),
                "result": self.result(cur) if parser(cur["type"] or "") else None,
            }
        return out

    # ------------------------------------------------------------ actions

    def toggle(self) -> None:
        cur = self.live.current()
        if not cur or not cur.get("capture"):
            raise LiveError(409, "not_capturable", "Space captures answers on an activity — this item has none")
        if self.status(cur["id"]) == "live":
            self.stop(cur["id"], by_hand=True)
        else:
            self.start(cur["id"])

    def start(self, item_id: str) -> None:
        other = self.active_item()
        if other and other != item_id:
            self.stop(other, by_hand=True)  # one capture at a time
        self.windows.setdefault(item_id, []).append([now_ms(), None])
        reopened = len(self.windows[item_id]) > 1
        logger.info("ℹ️ capture %s %s", "reopened on" if reopened else "started on", item_id)
        self.live._event("capture_reopen" if reopened else "capture_start", item_id=item_id)
        item = self.live.item_by_id(item_id)
        timer = (item or {}).get("timer") or {}
        self._save()
        if timer.get("start") == "with_capture":
            self.live.start_timer_for(item_id)  # commits
        else:
            self.live._commit()

    def stop(self, item_id: str, *, by_hand: bool) -> None:
        wins = self.windows.get(item_id)
        if not wins or wins[-1][1] is not None:
            return
        wins[-1][1] = now_ms()
        item = self.live.item_by_id(item_id)
        count = len(self.messages_for(item_id))
        logger.info("ℹ️ capture stopped on %s (%d answers)", item_id, count)
        self.live._event("capture_stop", item_id=item_id, answers=count)
        self._save()
        if item is not None:
            self._freeze(item)
        timer = (item or {}).get("timer") or {}
        if by_hand and timer.get("start") == "with_capture":
            self.live.pause_timer_for(item_id)  # commits
        else:
            self.live._commit()

    def set_hidden(self, message_id: int, hidden: bool) -> None:
        if hidden:
            self.hidden.add(message_id)
        else:
            self.hidden.discard(message_id)
        self.live._event("hide" if hidden else "unhide", message_id=message_id)
        self._save()
        # A frozen capture that contained it is re-frozen so the files match what counts.
        for item_id in list(self.windows):
            if self.status(item_id) == "stopped" and any(m["id"] == message_id for m in self.messages_for(item_id, include_hidden=True)):
                item = self.live.item_by_id(item_id)
                if item is not None:
                    self._freeze(item)
        self.live._commit()

    def place(self, message_id: int, geonameid: str) -> None:
        """Fix an unplaced map answer by hand (the presenter's one-click fix)."""
        self.places[message_id] = geonameid
        self.live._event("place", message_id=message_id, geonameid=geonameid)
        self._save()
        for item_id in list(self.windows):
            if self.status(item_id) == "stopped" and any(m["id"] == message_id for m in self.messages_for(item_id, include_hidden=True)):
                item = self.live.item_by_id(item_id)
                if item is not None:
                    self._freeze(item)
        self.live._commit()

    # ------------------------------------------------------------- hooks

    def _on_item(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        # Each activity opens with its own "show names" option; the presenter can flip it.
        if cur.get("kind") == "activity" and cur.get("type"):
            opts = options_with_defaults(cur["type"], cur.get("options") or {})
            self.live.names = bool(opts.get("show_names", False))

    def _on_timer_end(self, item: dict[str, Any], end: str) -> None:
        if end == "stop_capture" and self.status(item["id"]) == "live":
            self.stop(item["id"], by_hand=False)

    def _on_messages(self, records: list[dict[str, Any]]) -> None:
        active = self.active_item()
        if active is None or self._push_pending or self.live.loop is None:
            return
        cur = self.live.current()
        if not cur or cur["id"] != active:
            return
        self._push_pending = True

        def push() -> None:
            self._push_pending = False
            self.live.rev += 1
            self.live.broadcast(self.live.snapshot())

        self.live.loop.call_later(PUSH_EVERY_S, push)

    # -------------------------------------------------------- durability

    def _dir(self) -> Optional[Path]:
        return self.live.folder / "live" if self.live.folder is not None else None

    def _load(self) -> None:
        if self.live.session_id == self._session:
            return
        self._session = self.live.session_id
        self.windows, self.hidden, self.places = {}, set(), {}
        d = self._dir()
        if d is None or not (d / STATE_FILE).is_file():
            return
        try:
            data = json.loads((d / STATE_FILE).read_text(encoding="utf-8"))
            self.windows = {k: [list(w) for w in v] for k, v in (data.get("windows") or {}).items()}
            self.hidden = {int(x) for x in data.get("hidden") or []}
            self.places = {int(k): str(v) for k, v in (data.get("places") or {}).items()}
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("⚠️ %s unreadable (%s) — captures start empty", STATE_FILE, exc)

    def _save(self) -> None:
        d = self._dir()
        if d is None:
            return
        try:
            atomic_write_text(d / STATE_FILE, json.dumps({"windows": self.windows, "hidden": sorted(self.hidden),
                                                          "places": {str(k): v for k, v in self.places.items()}}, indent=1))
        except OSError as exc:
            self.live._write_failed(STATE_FILE, exc)

    def frozen_path(self, item_id: str, ext: str) -> Optional[Path]:
        d = self._dir()
        return d / "captures" / f"{item_id}.{ext}" if d is not None else None

    def _freeze(self, item: dict[str, Any]) -> None:
        path = self.frozen_path(item["id"], "json")
        if path is None:
            return
        answers = []
        mod = parser(item.get("type") or "")
        opts = options_with_defaults(item.get("type") or "", item.get("options") or {})
        for m in self.messages_for(item["id"], include_hidden=True):
            parsed = None
            if mod is not None and m["id"] not in self.hidden:
                try:
                    parsed = mod.parse(m, opts)
                except Exception:  # noqa: BLE001
                    parsed = None
            answers.append({"id": m["id"], "sender": m["sender"], "text": m["text"], "time": m["time"],
                            "received_at": m["received_at"], "hidden": m["id"] in self.hidden, "parsed": parsed})
        frozen = {
            "item": item, "frozen_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "windows": self.windows.get(item["id"], []), "answers": answers, "result": self.result(item),
            "names": self.live.names,
        }
        try:
            atomic_write_text(path, json.dumps(frozen, ensure_ascii=False, indent=1))
        except OSError as exc:
            self.live._write_failed(f"captures/{path.name}", exc)
            return
        self._render_png(item["id"])

    def _render_png(self, item_id: str) -> None:
        out = self.frozen_path(item_id, "png")
        if out is None or not self.freeze_url or self.live.session_id is None:
            return
        url = f"{self.freeze_url}/stage?freeze={item_id}&session={self.live.session_id}"
        try:
            subprocess.Popen([sys.executable, "-m", "src.live.freeze", url, str(out)], cwd=PROJECT_ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except OSError as exc:
            logger.error("❌ capture PNG of %s not started: %s", item_id, exc)

    def frozen(self, item_id: str) -> Optional[dict[str, Any]]:
        path = self.frozen_path(item_id, "json")
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _int(arg: Optional[str]) -> int:
    try:
        return int(str(arg))
    except (TypeError, ValueError) as exc:
        raise LiveError(422, "bad_argument", "message id must be a number") from exc
