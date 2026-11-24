"""The session room, wired to the live hub and persisted to ``live/room.jsonl``.

A live session has one room: one 6-digit PIN and one QR, which a phone joins
once and stays joined to for every activity of the run (the quiz is the first:
``src/quiz/service.py`` takes its PIN and its phone players from here).

**Lifecycle.** The room opens when the session goes live (or the first time
anything asks for it while the session is live) and is rebuilt by replaying
``room.jsonl``, so the PIN and the members survive a restart. Closing the
session closes the room: the PIN takes no more joins. Taking the session live
again opens a new room with a new PIN. A reset sets ``live/`` aside, the old
room with it, and opens a fresh one.

**In-process API.** Every call runs on the live hub's event loop, as the quiz's does:

- ``pin()`` — the open room's PIN (``""`` with none open).
- ``pin_matches(pin)`` — the PIN typed (spaces ignored) is the open room's.
- ``join(name, key=None) -> RoomJoin`` — ``joined`` with ``player_id`` +
  ``secret``; ``name`` may be empty (anonymous). The same ``key`` returns the
  same member. ``closed`` with no open room.
- ``resume(player_id, secret) -> RoomJoin`` — ``resumed``, ``closed`` or
  ``unknown_player`` (also for a wrong secret).
- ``member(player_id)`` — that member, open room or not.
- ``close()`` — the room closes and its PIN dies.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.jsonl import JsonlAppender, read_jsonl
from src.live.hub import LiveHub, now_ms
from src.room.model import (
    CLOSED,
    JOINED,
    RESUMED,
    Member,
    Room,
    clean_member_name,
    clean_pin,
    replay,
)

logger = logging.getLogger(__name__)

ROOM_FILE = "room.jsonl"
PIN_DIGITS = 6


@dataclass
class RoomJoin:
    state: str
    player_id: Optional[str] = None
    secret: Optional[str] = None
    name: Optional[str] = None


class RoomService:
    def __init__(self, live: LiveHub, clock: Callable[[], int] = now_ms) -> None:
        self.live, self.clock = live, clock
        self.room: Optional[Room] = None
        # Ids other services hand out to players that never joined the room (the quiz's chat players).
        self.id_taken: Callable[[str], bool] = lambda pid: False
        self._session: Optional[str] = None
        self._log = JsonlAppender(ROOM_FILE, live.write_failed, live.write_ok)
        live.extra_state.append(self.state_fields)
        live.session_listeners.append(self._on_session)
        live.close_listeners.append(self.close)
        live.reset_listeners.append(self._on_reset)

    # ------------------------------------------------------------ queries

    def current(self) -> Optional[Room]:
        """The live session's room, opened now if the session is live and has none open."""
        self._load()
        if self.live.session_id is not None and self.live.folder is not None and (self.room is None or self.room.closed):
            self._open()
        return self.room

    def pin(self) -> str:
        room = self.current()
        return room.pin if room is not None and room.open else ""

    def pin_matches(self, pin: Any) -> bool:
        mine = self.pin()
        return bool(mine) and clean_pin(pin) == mine

    def member(self, player_id: str) -> Optional[Member]:
        room = self.current()
        return room.members.get(player_id) if room is not None else None

    def state_fields(self) -> dict[str, Any]:
        room = self.current()
        return {"room": room.snapshot() if room is not None else None}

    # ------------------------------------------------------------ members

    def join(self, name: Any, key: Optional[str] = None) -> RoomJoin:
        """A phone joins the room (see the module docstring)."""
        room = self.current()
        if room is None or not room.open:
            return RoomJoin(CLOSED)
        known = room.by_key(key)
        if known is not None:
            return RoomJoin(JOINED, known.id, known.secret, known.name)
        player_id, secret = self._new_player_id(room), secrets.token_urlsafe(16)
        clean = clean_member_name(name)
        self._record(room, {"op": "join", "player_id": player_id, "name": clean, "secret": secret, "key": key})
        logger.info("ℹ️ room %s: %s joined (%d members)", room.room_id, player_id, len(room.members))
        return RoomJoin(JOINED, player_id, secret, clean)

    def resume(self, player_id: str, secret: str) -> RoomJoin:
        """The same member again (a reopened tab, a locked phone, a network switch)."""
        room = self.current()
        refused = room.check(player_id, secret) if room is not None else CLOSED
        if refused:
            return RoomJoin(refused, player_id)
        self._record(room, {"op": "resume", "player_id": player_id})
        m = room.members[player_id]
        return RoomJoin(RESUMED, m.id, m.secret, m.name)

    def close(self) -> None:
        """Close the open room: its PIN takes no more joins."""
        self._load()
        room = self.room
        if room is None or not room.open:
            return
        self._record(room, {"op": "close"})
        logger.info("ℹ️ room %s: closed — PIN %s no longer joins", room.room_id, room.pin)

    def _new_player_id(self, room: Room) -> str:
        while True:
            pid = "p" + secrets.token_hex(4)
            if pid not in room.members and not self.id_taken(pid):
                return pid

    def _open(self) -> None:
        old = self.room.pin if self.room is not None else ""
        while True:
            pin = str(10 ** (PIN_DIGITS - 1) + secrets.randbelow(9 * 10 ** (PIN_DIGITS - 1)))
            if pin != old:  # a reopened room never reuses the PIN that was just closed
                break
        self.room = Room("r" + secrets.token_hex(4))
        self._record(self.room, {"op": "open", "pin": pin})
        self.live.event("room_open", room=self.room.room_id)
        logger.info("✅ room %s: open (session %s)", self.room.room_id, self.live.session_id)

    # ------------------------------------------------------------- hooks

    def _on_session(self, sid: Optional[str]) -> None:
        if sid is not None:
            self.current()

    def _on_reset(self) -> None:
        """After a reset ``live/`` (the old room with it) is set aside: open a fresh room."""
        self._session = None
        self.current()

    # -------------------------------------------------------- durability

    def _path(self) -> Optional[Path]:
        return self.live.folder / "live" / ROOM_FILE if self.live.folder is not None else None

    def _load(self) -> None:
        """Rebuild the live session's room by replaying ``room.jsonl`` (once per session)."""
        if self.live.session_id == self._session:
            return
        self._session = self.live.session_id
        self.room = None
        self._log.forget()
        path = self._path()
        if path is None:
            return
        records = read_jsonl(path)
        self.room = replay(records)
        if self.room is not None:
            logger.info("✅ room %s: %d records replayed (%s)", self.room.room_id, len(records),
                        "open" if self.room.open else "closed")

    def _record(self, room: Room, rec: dict[str, Any]) -> None:
        """Apply one record to the room and append it to ``room.jsonl`` (kept in memory if the write fails)."""
        rec = {"room": room.room_id, "at": self.clock(), **rec}
        room.apply(rec)
        path = self._path()
        if path is not None:
            self._log.append(path, [rec])
