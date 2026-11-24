"""The session room: one PIN, the people who joined it — pure, no I/O, no clock.

**Event-sourced** like the quiz game (``src/quiz/engine.py``): every change is
one record and ``Room.apply`` is the only thing that changes a room, live and
on replay of ``live/room.jsonl`` alike. Records (every one also carries
``room`` and ``at``, epoch ms):

- ``{"op": "open", "pin"}`` — a room opens with its 6-digit join PIN.
- ``{"op": "join", "player_id", "name", "secret", "key"}`` — ``name`` may be
  empty (an anonymous member); ``key`` is the caller's optional idempotency
  key, so a retried join returns the same member.
- ``{"op": "resume", "player_id"}`` — the same member again (diagnostics only).
- ``{"op": "close"}`` — the room closes: its PIN takes no more joins and its
  members can no longer resume through it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_NAME = 20  # characters in a member's name, the same cap as a quiz nickname

# Join and resume results — each one a distinct state the phone can say something different for.
JOINED = "joined"
RESUMED = "resumed"
WRONG_PIN = "wrong_pin"  # no open room has that PIN
CLOSED = "closed"  # the room is closed
UNKNOWN_MEMBER = "unknown_player"  # no such member, or the secret does not match (the quiz's own word)


def clean_member_name(name: Any) -> str:
    """A member's name on one line, at most ``MAX_NAME`` characters; empty means anonymous."""
    return " ".join(str(name or "").split())[:MAX_NAME].strip()


def clean_pin(pin: Any) -> str:
    """``"123 456"`` → ``"123456"``: a PIN as typed, its spaces dropped."""
    return "".join(str(pin or "").split())


@dataclass
class Member:
    id: str
    name: str  # empty for an anonymous member
    secret: str
    key: Optional[str] = None


@dataclass
class Room:
    room_id: str
    pin: str = ""
    closed: bool = False
    members: dict[str, Member] = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return bool(self.pin) and not self.closed

    def apply(self, rec: dict[str, Any]) -> None:
        """Apply one record. Unknown records are ignored; a closed room changes no more."""
        op = rec.get("op")
        if op == "open":
            self.pin = str(rec["pin"])
        elif op == "close":
            self.closed = True
        elif op == "join" and not self.closed and rec["player_id"] not in self.members:
            self.members[rec["player_id"]] = Member(rec["player_id"], str(rec.get("name") or ""), rec["secret"],
                                                    rec.get("key"))
        # "resume" changes nothing in the room itself.

    def by_key(self, key: Optional[str]) -> Optional[Member]:
        return next((m for m in self.members.values() if m.key == key), None) if key else None

    def check(self, player_id: str, secret: Optional[str]) -> Optional[str]:
        """``None`` when the member may act, else ``CLOSED`` or ``UNKNOWN_MEMBER``."""
        m = self.members.get(player_id)
        if m is None or secret != m.secret:
            return UNKNOWN_MEMBER
        return CLOSED if self.closed else None

    def snapshot(self) -> dict[str, Any]:
        """The ``room`` field of the live snapshot."""
        return {"room_id": self.room_id, "pin": self.pin, "open": self.open, "member_count": len(self.members)}


def replay(records: list[dict[str, Any]]) -> Optional[Room]:
    """The newest room of ``room.jsonl``'s ``records`` (``None`` when there is none)."""
    rooms: dict[str, Room] = {}
    last: Optional[str] = None
    for rec in records:
        rid = rec.get("room")
        if not rid:
            continue
        if rec.get("op") == "open" and rid not in rooms:
            rooms[rid] = Room(rid)
            last = rid
        if rid in rooms:
            try:
                rooms[rid].apply(rec)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("⚠️ room: a damaged record skipped (%s)", exc)
    return rooms.get(last) if last else None
