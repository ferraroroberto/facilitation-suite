"""Room polls: phones in the session room answer the activity on stage.

People in the room answer on their phones while people online type in the
chat, and one capture counts both. A phone's answer is stored by
``ChatHub.add_room`` as one more message (``source: room``, with the phone's
``player_id``), so the activity's own parser counts it, hiding and excluded
senders work on it, and the offline replay sees it.

**Which activities.** A type whose answers a phone can give with one control:
the table in ``src/room/controls.py`` (text, scale, stars, choice, number).
Any other item shows the phone a "watch the screen" view.

**Rules.**

- An answer counts only while the capture of that item is live: before it
  starts and after it stops the phone gets ``not_open`` (the poll is closed).
- One answer per phone per poll: a new answer replaces the phone's earlier one
  until the poll closes (``select_messages`` keeps a phone's newest only).
- A retry carries the same ``key``: it gets the same ack and is never counted
  twice; an answer equal to the phone's current one adds nothing.
- The sender is the member's name, or ``Guest <4 characters of its id>`` for a
  member who joined without one, so anonymous phones stay distinct people.

**In-process API** (on the live hub's event loop, as the quiz's):

- ``view(player_id, secret) -> Optional[dict]`` — the phone's poll view while
  a phone-answerable activity is on stage: ``kind: poll``, ``item_id``,
  ``type``, ``control``, ``question``, ``open``, ``answer`` (the phone's
  counted answer or ``None``), the control's own fields (``controls.fields``:
  ``max_length``; ``min``/``max``/``labels``; ``choices``/``multiple``;
  ``min``/``max``/``unit``), ``lang``. ``None`` otherwise, or for
  someone who is not a member of the open room.
- ``answer(player_id, secret, item_id, value, key=None) -> PollAck`` —
  ``accepted`` (with the phone's counted ``answer``), ``not_open``,
  ``unknown_player`` or ``closed`` (the room closed). Raises ``controls.PollError`` 422
  ``bad_value`` for an answer its control cannot send (an empty text, a number
  out of range, an unknown choice, two choices where one is allowed).
- ``join_state() -> Optional[dict]`` — the live snapshot's ``join`` field (below).

**Joining from the stage.** A poll slide (an item a phone can answer) shows a
corner QR badge, and a Join slide (``room_join``) a large QR code, each with
the join link and the room's PIN. ``join`` is ``None`` off those items; on
them it is ``item_id``, ``pin``, ``url`` (the public join link, ``None`` while
it is not configured), ``member_count`` and ``ok``. ``ok`` is true only while
the room is open, the link is configured and the last public-link check
(``src/quiz/reach.py``) says ``ok``, so a QR that leads nowhere is never
shown; the chat prompt stays on the slide either way, so the chat is the
fallback. The server sets ``join_url`` and ``reachable``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from src.chat.hub import ChatHub
from src.live.capture import CaptureService
from src.live.hub import LiveHub
from src.room.controls import clean_value, control_of, fields
from src.room.model import CLOSED, ROOM_SOURCE
from src.room.service import RoomService

logger = logging.getLogger(__name__)

ACCEPTED = "accepted"
NOT_OPEN = "not_open"  # no live capture on that item: the poll has not started or is closed
JOIN_TYPE = "room_join"  # the Join slide: a large QR code, the join link and the PIN


@dataclass
class PollAck:
    state: str
    item_id: str
    answer: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "item_id": self.item_id, "answer": self.answer}


class RoomPolls:
    def __init__(self, live: LiveHub, room: RoomService, chat: ChatHub, capture: CaptureService) -> None:
        self.live, self.room, self.chat, self.capture = live, room, chat, capture
        self._session: Optional[str] = None
        self._latest: dict[tuple[str, str], str] = {}  # (item id, player id) → the phone's newest answer
        self._keys: dict[str, str] = {}  # a phone's answer key → the answer it carried
        self.join_url: Callable[[str], Optional[str]] = lambda pin: None  # the public join link (set by the server)
        self.reachable: Callable[[], bool] = lambda: False  # the last public-link check said ok (set by the server)
        chat.listeners.append(self._on_messages)
        live.reset_listeners.append(self._forget)
        live.extra_state.append(lambda: {"join": self.join_state()})

    # ------------------------------------------------------------ queries

    def view(self, player_id: str, secret: str) -> Optional[dict[str, Any]]:
        cur = self.live.current()
        control = control_of(cur)
        if cur is None or control is None or self.refused(player_id, secret):
            return None
        out: dict[str, Any] = {
            "kind": "poll", "item_id": cur["id"], "type": cur["type"], "control": control,
            "question": str(cur.get("question") or ""), "open": self._open(cur["id"]),
            "answer": self.answer_of(cur["id"], player_id), "lang": self.language(),
        }
        out.update(fields(cur))
        return out

    def join_state(self) -> Optional[dict[str, Any]]:
        """How a phone joins from the item on stage (see **Joining from the stage**)."""
        cur = self.live.current()
        if cur is None or (control_of(cur) is None and cur.get("type") != JOIN_TYPE):
            return None
        room = self.room.current()
        pin = room.pin if room is not None and room.open else ""
        url = self.join_url(pin) if pin else None
        return {"item_id": cur["id"], "pin": pin, "url": url,
                "member_count": len(room.members) if room is not None else 0,
                "ok": bool(url) and self.reachable()}

    def answer_of(self, item_id: str, player_id: str) -> Optional[str]:
        self._sync()
        return self._latest.get((item_id, player_id))

    def language(self) -> str:
        return str(self.live.run.get("language") or "en")

    def _open(self, item_id: str) -> bool:
        return self.capture.active_item() == item_id

    def refused(self, player_id: str, secret: Optional[str]) -> Optional[str]:
        """``None`` for a member of the open room with the right secret, else ``unknown_player`` or ``closed``."""
        room = self.room.current()
        return room.check(player_id, secret) if room is not None else CLOSED

    # ------------------------------------------------------------ answers

    def answer(self, player_id: str, secret: str, item_id: str, value: Any, key: Optional[str] = None) -> PollAck:
        self._sync()
        refused = self.refused(player_id, secret)
        if refused:
            return PollAck(refused, item_id)
        if key and key in self._keys:
            return PollAck(ACCEPTED, item_id, self._keys[key])  # a retry of an answer already counted
        cur = self.live.current()
        if cur is None or cur["id"] != item_id or control_of(cur) is None or not self._open(item_id):
            return PollAck(NOT_OPEN, item_id, self._latest.get((item_id, player_id)))
        text = clean_value(cur, value)
        if self._latest.get((item_id, player_id)) != text:
            member = self.room.member(player_id)
            name = member.name if member is not None and member.name else f"Guest {player_id[-4:]}"
            self.chat.add_room(name, text, player_id=player_id, item_id=item_id, key=key)
            logger.info("ℹ️ room poll %s: an answer from %s", item_id, player_id)
        if key:
            self._keys[key] = text
        return PollAck(ACCEPTED, item_id, text)

    # ------------------------------------------------------------- hooks

    def _on_messages(self, records: list[dict[str, Any]]) -> None:
        self._sync()
        for rec in records:
            self._index(rec)

    def _forget(self) -> None:
        self._session = None

    def _sync(self) -> None:
        """Rebuild the phones' answers from the live session's messages when the session changed."""
        self.chat.sync_session()
        if self._session == self.live.session_id:
            return
        self._session = self.live.session_id
        self._latest, self._keys = {}, {}
        for rec in self.chat.messages:
            self._index(rec)

    def _index(self, rec: dict[str, Any]) -> None:
        if rec.get("source") != ROOM_SOURCE or not rec.get("player_id") or not rec.get("item_id"):
            return
        self._latest[(str(rec["item_id"]), str(rec["player_id"]))] = str(rec.get("text") or "")
        if rec.get("key"):
            self._keys[str(rec["key"])] = str(rec.get("text") or "")
