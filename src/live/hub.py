"""The live session: one server-owned state, pushed to every view over ``/ws``.

The stage, the presenter (and later the phone remote and the Stream Deck)
only send **intents** (``next``, ``timer_toggle``, …, see ``actions.py``);
this hub is the only thing that mutates the state, and it pushes a full
snapshot to every connected client after each change. Snapshots are small
(positions, clocks, timers — never slide images), so there are no deltas to
get out of order.

Threading: every mutation runs on the server's event loop. The one entry
point called from a worker thread — ``session_saved`` (the Plan tab's PUT) —
hops onto the loop with ``call_soon_threadsafe``.

Clocks are wall-clock epochs in milliseconds; each snapshot carries
``server_now`` so a client can correct for its own clock skew.

Timers are per item (epic §9): an item's timer state is created when it is
first started and lives until reset. A timer keeps running when the
presenter moves on (like a real kitchen timer), but its end behaviour
``advance`` only fires while its item is still on stage.

Durability (epic §5.4): the position, clocks and timers are mirrored to
``live/state.json`` so a restarted server resumes where it was, and every
item change, clock and timer event is appended to ``live/events.jsonl``
(which drives the session PDF order in step 12). A failed write is logged,
kept in memory and surfaced on the presenter as ``write_error``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from src.groups.roster import RosterError, load_groups
from src.live.plan import build_run
from src.sessions.readiness import slides_meta
from src.sessions.store import SessionError, SessionStore, atomic_write_text

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
EVENTS_FILE = "events.jsonl"
ROLES = ("stage", "presenter", "remote", "app")


def now_ms() -> int:
    return int(time.time() * 1000)


def _rounds(folder: Path) -> Optional[dict[str, Any]]:
    try:
        return load_groups(folder).get("rounds")
    except RosterError as exc:
        logger.warning("⚠️ live: %s — the reveal shows no rooms", exc)
        return None


class LiveError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(eq=False)
class Client:
    role: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    width: int = 0
    height: int = 0

    def push(self, message: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            # A client that stopped reading gets a fresh snapshot, not a backlog.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(message)


@dataclass
class TimerState:
    total: int
    elapsed: float = 0.0
    running_since: Optional[int] = None
    done: bool = False

    def remaining(self, at_ms: int) -> float:
        running = (at_ms - self.running_since) / 1000 if self.running_since is not None else 0.0
        return self.total - self.elapsed - running

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "elapsed": round(self.elapsed, 3), "running_since": self.running_since, "done": self.done}


class LiveHub:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.clients: set[Client] = set()
        self.session_id: Optional[str] = None
        self.folder: Optional[Path] = None
        self.session_title = ""
        self.duration_minutes = 0
        self.run: dict[str, Any] = {"items": [], "sections": [], "planned_minutes": 0}
        self.plan_rev = 0
        self.index = 0
        self.blackout = False
        self.names = False
        self.clock_started_at: Optional[int] = None
        self.section_entered: dict[str, int] = {}
        self.timers: dict[str, TimerState] = {}
        self._timer_handles: dict[str, asyncio.TimerHandle] = {}
        self.write_error: Optional[str] = None
        self.rev = 0
        # Step hooks: later services (capture, OBS) subscribe to item changes and timer ends.
        self.item_listeners: list[Callable[[Optional[dict[str, Any]], dict[str, Any]], None]] = []
        self.timer_end_listeners: list[Callable[[dict[str, Any], str], None]] = []
        self.extra_state: list[Callable[[], dict[str, Any]]] = []
        self.session_listeners: list[Callable[[Optional[str]], None]] = []
        # The camera zone of each OBS profile (Settings); the defaults until the server wires it.
        self.zones: Callable[[], dict[str, Any]] = lambda: {}

    # ------------------------------------------------------------------ setup

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    # ---------------------------------------------------------------- queries

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.run["items"]

    def current(self) -> Optional[dict[str, Any]]:
        return self.items[self.index] if self.items and 0 <= self.index < len(self.items) else None

    def item_by_id(self, item_id: str) -> Optional[dict[str, Any]]:
        return next((it for it in self.items if it["id"] == item_id), None)

    def plan_message(self) -> dict[str, Any]:
        return {
            "type": "plan",
            "rev": self.plan_rev,
            "active": self.session_id is not None,
            "session": {"id": self.session_id, "title": self.session_title, "duration_minutes": self.duration_minutes},
            "run": self.run,
        }

    def snapshot(self) -> dict[str, Any]:
        cur = self.current()
        stages = [c for c in self.clients if c.role == "stage"]
        state: dict[str, Any] = {
            "active": self.session_id is not None,
            "session_id": self.session_id,
            "plan_rev": self.plan_rev,
            "index": self.index,
            "item_id": cur["id"] if cur else None,
            "count": len(self.items),
            "blackout": self.blackout,
            "names": self.names,
            "clock": {"started_at": self.clock_started_at, "duration_minutes": self.duration_minutes,
                      "planned_minutes": self.run.get("planned_minutes", 0)},
            "section_entered": dict(self.section_entered),
            "timers": {k: v.as_dict() for k, v in self.timers.items()},
            "stages": [{"w": c.width, "h": c.height} for c in stages],
            "presenters": sum(1 for c in self.clients if c.role == "presenter"),
            "write_error": self.write_error,
            "rev": self.rev,
        }
        for extra in self.extra_state:
            state.update(extra())
        return {"type": "state", "server_now": now_ms(), "state": state}

    # ------------------------------------------------------------ connections

    def connect(self, role: str) -> Client:
        client = Client(role=role if role in ROLES else "app")
        self.clients.add(client)
        client.push(self.plan_message())
        client.push(self.snapshot())
        logger.info("ℹ️ live: %s connected (%d clients)", client.role, len(self.clients))
        if client.role == "stage":
            self._broadcast_state()
        return client

    def disconnect(self, client: Client) -> None:
        self.clients.discard(client)
        logger.info("ℹ️ live: %s disconnected (%d clients)", client.role, len(self.clients))
        if client.role == "stage":
            self._broadcast_state()

    def hello(self, client: Client, width: int, height: int) -> None:
        client.width, client.height = int(width), int(height)
        self._broadcast_state()

    def broadcast(self, message: dict[str, Any]) -> None:
        for c in list(self.clients):
            c.push(message)

    def _broadcast_state(self) -> None:
        self.broadcast(self.snapshot())

    def push_state(self) -> None:
        """Another service's state changed (OBS, the reader): push a fresh snapshot."""
        self.rev += 1
        self._broadcast_state()

    def _commit(self) -> None:
        """After every mutation: bump the revision, persist, push to everyone."""
        self.rev += 1
        self._save_state()
        self._broadcast_state()

    # ------------------------------------------------------------- lifecycle

    def activate(self, sid: str) -> None:
        if sid == self.session_id:
            return
        try:
            folder = self.store.folder(sid)
            session = self.store.load(sid)
        except SessionError as exc:
            raise LiveError(exc.status, exc.code, str(exc)) from exc
        self._cancel_timer_handles()
        self.session_id, self.folder = sid, folder
        self.session_title, self.duration_minutes = session.title, session.duration_minutes
        self.run = build_run(session, slides_meta(folder), _rounds(folder), self.zones())
        self.plan_rev += 1
        self.index, self.blackout, self.names = 0, False, False
        self.clock_started_at, self.section_entered, self.timers = None, {}, {}
        self.write_error = None
        self._restore_state()
        logger.info("✅ live: session %s is live (%d items, resumed at %d)", sid, len(self.items), self.index + 1)
        self._event("session_live", items=len(self.items))
        for fn in self.session_listeners:
            fn(sid)
        self.broadcast(self.plan_message())
        self._commit()

    def deactivate(self) -> None:
        if self.session_id is None:
            return
        logger.info("ℹ️ live: session %s is no longer live", self.session_id)
        self._event("session_closed")
        self._cancel_timer_handles()
        self.session_id, self.folder = None, None
        self.run = {"items": [], "sections": [], "planned_minutes": 0}
        self.plan_rev += 1
        self.index = 0
        self.timers, self.section_entered, self.clock_started_at = {}, {}, None
        for fn in self.session_listeners:
            fn(None)
        self.broadcast(self.plan_message())
        self.rev += 1
        self._broadcast_state()

    def session_saved(self, sid: str) -> None:
        """Called from the Plan tab's save (a worker thread): reload if it is live."""
        if sid != self.session_id or self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._reload, sid)

    def _reload(self, sid: str) -> None:
        if sid != self.session_id or self.folder is None:
            return
        try:
            session = self.store.load(sid)
        except SessionError as exc:
            logger.warning("⚠️ live: reload of %s failed (%s) — keeping the previous plan", sid, exc)
            return
        cur = self.current()
        self.session_title, self.duration_minutes = session.title, session.duration_minutes
        self.run = build_run(session, slides_meta(self.folder), _rounds(self.folder), self.zones())
        self.plan_rev += 1
        found = self.item_by_id(cur["id"]) if cur else None
        self.index = found["index"] if found else min(self.index, max(0, len(self.items) - 1))
        logger.info("ℹ️ live: plan reloaded (%d items, at %d)", len(self.items), self.index + 1)
        self.broadcast(self.plan_message())
        self._commit()

    # -------------------------------------------------------------- movement

    def goto(self, index: int) -> None:
        if not self.items:
            raise LiveError(409, "not_live", "No session is live")
        index = max(0, min(int(index), len(self.items) - 1))
        if index == self.index:
            return
        prev = self.current()
        self.index = index
        cur = self.items[index]
        self._enter_section(cur["section_id"])
        timer = cur.get("timer")
        if timer and timer.get("start") == "on_enter" and cur["id"] not in self.timers:
            self._timer_start(cur)
        self._event("item", item_id=cur["id"], index=index, kind=cur["kind"])
        for fn in self.item_listeners:
            fn(prev, cur)
        self._commit()

    def next(self) -> None:
        self.goto(self.index + 1)

    def prev(self) -> None:
        self.goto(self.index - 1)

    def goto_section(self, number: int) -> None:
        secs = self.run["sections"]
        if not 1 <= number <= len(secs) or secs[number - 1]["first_index"] is None:
            raise LiveError(404, "no_section", f"No section {number} with items")
        self.goto(secs[number - 1]["first_index"])

    def toggle_blackout(self) -> None:
        self.blackout = not self.blackout
        self._event("blackout", on=self.blackout)
        self._commit()

    def toggle_names(self) -> None:
        self.names = not self.names
        self._commit()

    # ---------------------------------------------------------------- clocks

    def _enter_section(self, section_id: str) -> None:
        if self.clock_started_at is not None and section_id not in self.section_entered:
            self.section_entered[section_id] = now_ms()

    def clock_start(self) -> None:
        if self.clock_started_at is not None:
            return
        self.clock_started_at = now_ms()
        cur = self.current()
        if cur:
            self.section_entered = {cur["section_id"]: self.clock_started_at}
        self._event("clock_start")
        self._commit()

    def clock_reset(self) -> None:
        self.clock_started_at = None
        self.section_entered = {}
        self._event("clock_reset")
        self._commit()

    # ---------------------------------------------------------------- timers

    def _timer_item(self) -> dict[str, Any]:
        cur = self.current()
        if not cur:
            raise LiveError(409, "not_live", "No session is live")
        if not cur.get("timer"):
            raise LiveError(409, "no_timer", "This item has no timer — add one in the Plan tab")
        return cur

    def _timer_start(self, item: dict[str, Any]) -> None:
        t = self.timers.get(item["id"])
        if t is None or t.done:
            t = TimerState(total=int(item["timer"]["seconds"]))
            self.timers[item["id"]] = t
        if t.running_since is None:
            t.running_since = now_ms()
            self._schedule_end(item["id"])
            self._event("timer_start", item_id=item["id"], remaining=round(t.remaining(now_ms())))

    def _timer_pause(self, item_id: str) -> None:
        t = self.timers.get(item_id)
        if t is None or t.running_since is None:
            return
        t.elapsed += (now_ms() - t.running_since) / 1000
        t.running_since = None
        self._cancel_handle(item_id)
        self._event("timer_pause", item_id=item_id, remaining=round(t.remaining(now_ms())))

    def start_timer_for(self, item_id: str) -> None:
        """Start an item's timer from another service (capture start, step 7)."""
        item = self.item_by_id(item_id)
        if item and item.get("timer") and (item_id not in self.timers or self.timers[item_id].done
                                           or self.timers[item_id].running_since is None):
            self._timer_start(item)
            self._commit()

    def pause_timer_for(self, item_id: str) -> None:
        if item_id in self.timers and self.timers[item_id].running_since is not None:
            self._timer_pause(item_id)
            self._commit()

    def timer_toggle(self) -> None:
        item = self._timer_item()
        t = self.timers.get(item["id"])
        if t is not None and t.running_since is not None:
            self._timer_pause(item["id"])
        else:
            self._timer_start(item)
        self._commit()

    def timer_add_minute(self) -> None:
        item = self._timer_item()
        t = self.timers.get(item["id"])
        if t is None:
            t = self.timers[item["id"]] = TimerState(total=int(item["timer"]["seconds"]))
        if t.done:
            # "One more minute" after the end: a fresh minute, running.
            t.total, t.elapsed, t.done = 60, 0.0, False
            t.running_since = now_ms()
        else:
            t.total += 60
        if t.running_since is not None:
            self._schedule_end(item["id"])
        self._event("timer_add_minute", item_id=item["id"])
        self._commit()

    def timer_reset(self) -> None:
        item = self._timer_item()
        self._cancel_handle(item["id"])
        self.timers.pop(item["id"], None)
        self._event("timer_reset", item_id=item["id"])
        self._commit()

    def _schedule_end(self, item_id: str) -> None:
        self._cancel_handle(item_id)
        t = self.timers[item_id]
        if self.loop is None:
            return
        delay = max(0.0, t.remaining(now_ms()))
        self._timer_handles[item_id] = self.loop.call_later(delay, self._timer_end, item_id)

    def _cancel_handle(self, item_id: str) -> None:
        h = self._timer_handles.pop(item_id, None)
        if h is not None:
            h.cancel()

    def _cancel_timer_handles(self) -> None:
        for h in self._timer_handles.values():
            h.cancel()
        self._timer_handles = {}

    def _timer_end(self, item_id: str) -> None:
        self._timer_handles.pop(item_id, None)
        t = self.timers.get(item_id)
        item = self.item_by_id(item_id)
        if t is None or item is None or t.running_since is None:
            return
        if t.remaining(now_ms()) > 0.05:  # a minute was added meanwhile
            self._schedule_end(item_id)
            return
        t.elapsed, t.running_since, t.done = float(t.total), None, True
        end = (item.get("timer") or {}).get("end", "keep")
        logger.info("ℹ️ live: timer of %s ended (%s)", item_id, end)
        self._event("timer_end", item_id=item_id, end=end)
        for fn in self.timer_end_listeners:
            fn(item, end)
        if end == "chime":
            self.broadcast({"type": "chime", "item_id": item_id})
        if end == "advance" and self.current() is item and self.index < len(self.items) - 1:
            self.goto(self.index + 1)
            return
        self._commit()

    # ------------------------------------------------------------ durability

    def _live_dir(self) -> Optional[Path]:
        return self.folder / "live" if self.folder is not None else None

    def _write_failed(self, what: str, exc: OSError) -> None:
        msg = f"Could not write {what} in the session folder — kept in memory"
        if self.write_error != msg:
            logger.error("❌ live: %s (%s)", msg, exc)
        self.write_error = msg

    def _event(self, event: str, **fields: Any) -> None:
        live = self._live_dir()
        if live is None:
            return
        rec = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event, **fields}
        try:
            live.mkdir(parents=True, exist_ok=True)
            with open(live / EVENTS_FILE, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._write_failed(EVENTS_FILE, exc)

    def _save_state(self) -> None:
        live = self._live_dir()
        cur = self.current()
        if live is None:
            return
        data = {
            "session_id": self.session_id, "item_id": cur["id"] if cur else None, "blackout": self.blackout,
            "names": self.names, "clock_started_at": self.clock_started_at, "section_entered": self.section_entered,
            "timers": {k: v.as_dict() for k, v in self.timers.items()},
        }
        try:
            atomic_write_text(live / STATE_FILE, json.dumps(data, indent=1))
        except OSError as exc:
            self._write_failed(STATE_FILE, exc)

    def _restore_state(self) -> None:
        live = self._live_dir()
        if live is None:
            return
        try:
            data = json.loads((live / STATE_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ live: %s unreadable (%s) — starting from the first item", STATE_FILE, exc)
            return
        found = self.item_by_id(data.get("item_id") or "")
        self.index = found["index"] if found else 0
        self.blackout = bool(data.get("blackout"))
        self.names = bool(data.get("names"))
        self.clock_started_at = data.get("clock_started_at")
        self.section_entered = dict(data.get("section_entered") or {})
        for item_id, raw in (data.get("timers") or {}).items():
            if self.item_by_id(item_id) is None:
                continue
            t = TimerState(total=int(raw.get("total", 0)), elapsed=float(raw.get("elapsed", 0)),
                           running_since=raw.get("running_since"), done=bool(raw.get("done")))
            self.timers[item_id] = t
            if t.running_since is not None:
                self._schedule_end(item_id)
