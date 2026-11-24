"""Feedback forms during a live session, persisted to ``live/forms.jsonl``.

A form item (``src/forms/model.py``) opens the first time it comes on stage
and stays open after the stage moves on, so people can finish it during the
rest of the session; the presenter closes and reopens it by hand. Closing the
session closes every form. While a form is open its link
(``<public url>/play/form?t=<token>``) takes submissions; the token stays the
same for the session, so a link pasted in the chat works again after a reopen.
The link also needs the session room open: no live session, no form.

**Privacy.** Answers live here and in ``live/captures/<item>.csv`` only. The
live snapshot (the stage, the presenter, every ``/ws`` client) carries the
form's open state and link and, when the form's ``show_count`` is on, its
response count — never an answer. A phone gets its own acknowledgement only.

Records (each with ``item`` and ``at``, epoch ms):

- ``{"op": "open", "token"}`` / ``{"op": "close"}``
- ``{"op": "submit", "response_id", "name", "answers", "key"}`` — ``key`` is the
  phone's retry key, so a resent submission is stored once.

**In-process API** (on the live hub's event loop):

- ``open(item_id)`` / ``close(item_id)`` — ``FormError`` 404 for an item that is not a form.
- ``form_for(token) -> (item, public form)`` — ``FormError`` 410 ``closed`` while
  the form is closed, the room is closed or the token is unknown.
- ``submit(token, name, answers, key=None) -> dict`` — ``{"state": "accepted",
  "response_id"}``; ``FormError`` 422 ``bad_answers`` with ``detail.errors`` per question.
- ``results(item_id) -> dict`` — the backstage view: ``open``, ``url``, ``count``,
  each question's aggregate, ``csv`` (the file's name under ``live/captures/``).
- ``csv(item_id) -> str`` — the responses as CSV text (``model.csv_text``).
- ``room_view() -> Optional[dict]`` — a room phone's view while an open form is
  on stage: ``kind: form``, ``title``, ``link`` (the form page's path).
- ``join_state(item) -> dict`` — the stage's ``join`` for a form item: ``url``,
  ``ok`` (the link is configured and reachable), ``open``, ``count`` (``None``
  unless ``show_count``).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from src.atomic_write import atomic_write_text
from src.forms.model import (
    FORM_TYPE,
    FormError,
    clean_answers,
    clean_name,
    csv_text,
    public_form,
    questions,
    results,
)
from src.jsonl import JsonlAppender, read_jsonl
from src.live.hub import LiveHub, now_ms
from src.room.service import RoomService

logger = logging.getLogger(__name__)

FORMS_FILE = "forms.jsonl"
FORM_PATH = "/play/form"


@dataclass
class FormState:
    token: str
    open: bool = False
    opened_ever: bool = False
    responses: list[dict[str, Any]] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)  # a phone's retry key → the response it stored


class FormsService:
    def __init__(self, live: LiveHub, room: RoomService, clock: Callable[[], int] = now_ms) -> None:
        self.live, self.room, self.clock = live, room, clock
        self.forms: dict[str, FormState] = {}
        self._session: Optional[str] = None
        self._log = JsonlAppender(FORMS_FILE, live.write_failed, live.write_ok)
        self.public_url: Callable[[], str] = lambda: ""  # quiz.public_url (set by the server)
        self.reachable: Callable[[], bool] = lambda: False  # the last public-link check said ok (set by the server)
        live.item_listeners.append(self._on_item)
        live.close_listeners.append(self.close_all)
        live.reset_listeners.append(self._forget)
        live.extra_state.append(lambda: {"forms_open": self.open_ids()})

    # ------------------------------------------------------------ queries

    def open_ids(self) -> list[str]:
        self._load()
        return [iid for iid, f in self.forms.items() if f.open]

    def link(self, item_id: str) -> Optional[str]:
        """The form page's path on the player app (``None`` before the form was ever opened)."""
        self._load()
        f = self.forms.get(item_id)
        return f"{FORM_PATH}?t={quote(f.token)}" if f is not None else None

    def url(self, item_id: str) -> Optional[str]:
        base = (self.public_url() or "").strip().rstrip("/")
        path = self.link(item_id)
        return f"{base}{path}" if base and path else None

    def form_for(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self._load()
        item_id = next((iid for iid, f in self.forms.items() if token and f.token == token), None)
        item = self.live.item_by_id(item_id) if item_id else None
        if item is None or not self.forms[item_id].open or not self.room.pin():
            raise FormError(410, "closed", "This form is closed")
        return item, public_form(item)

    def results(self, item_id: str) -> dict[str, Any]:
        item = self._form_item(item_id)
        f = self.forms.get(item_id)
        responses = f.responses if f is not None else []
        out = results(questions(item.get("options")), responses)
        return {"item_id": item_id, "title": item.get("title") or "", "open": bool(f and f.open),
                "link": self.link(item_id), "url": self.url(item_id), "csv": f"{item_id}.csv" if responses else None,
                **out}

    def csv(self, item_id: str) -> str:
        item = self._form_item(item_id)
        f = self.forms.get(item_id)
        return csv_text(questions(item.get("options")), f.responses if f is not None else [])

    def room_view(self) -> Optional[dict[str, Any]]:
        cur = self.live.current()
        if cur is None or cur.get("type") != FORM_TYPE or cur["id"] not in self.open_ids():
            return None
        return {"kind": "form", "title": str(cur.get("title") or ""), "link": self.link(cur["id"])}

    def join_state(self, item: dict[str, Any]) -> dict[str, Any]:
        self._load()
        f = self.forms.get(item["id"])
        url = self.url(item["id"])
        is_open = bool(f and f.open)
        show = bool((item.get("options") or {}).get("show_count"))
        return {"item_id": item["id"], "url": url, "ok": bool(url) and is_open and self.reachable(),
                "open": is_open, "count": len(f.responses) if f is not None and show else None}

    # ------------------------------------------------------------ changes

    def open(self, item_id: str) -> None:
        self._form_item(item_id)
        f = self.forms.get(item_id)
        if f is not None and f.open:
            return
        token = f.token if f is not None else secrets.token_urlsafe(12)
        self._record(item_id, {"op": "open", "token": token})
        logger.info("✅ form %s: open", item_id)
        self.live.push_state()

    def close(self, item_id: str) -> None:
        self._form_item(item_id)
        f = self.forms.get(item_id)
        if f is None or not f.open:
            return
        self._record(item_id, {"op": "close"})
        logger.info("ℹ️ form %s: closed — its link takes no more responses (%d in)", item_id, len(f.responses))
        self.live.push_state()

    def close_all(self) -> None:
        """The session is closing: every open form closes with it."""
        for item_id in self.open_ids():
            self._record(item_id, {"op": "close"})
            logger.info("ℹ️ form %s: closed with the session", item_id)

    def submit(self, token: str, name: Any, answers: Any, key: Optional[str] = None) -> dict[str, Any]:
        item, _ = self.form_for(token)
        f = self.forms[item["id"]]
        if key and key in f.keys:
            return {"state": "accepted", "response_id": f.keys[key]}  # a resent submission, stored once
        clean = clean_answers(questions(item.get("options")), answers)
        rid = "r" + secrets.token_hex(4)
        self._record(item["id"], {"op": "submit", "response_id": rid, "name": clean_name(name), "answers": clean,
                                  "key": key})
        logger.info("ℹ️ form %s: a response (%d in)", item["id"], len(f.responses))
        self._write_csv(item)
        if (item.get("options") or {}).get("show_count"):
            self.live.push_state()  # the stage's count; no answer is in the snapshot
        return {"state": "accepted", "response_id": rid}

    # ------------------------------------------------------------- hooks

    def _on_item(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        """A form opens by itself the first time it comes on stage; after that only by hand."""
        if cur.get("type") != FORM_TYPE:
            return
        self._load()
        f = self.forms.get(cur["id"])
        if f is None or not f.opened_ever:
            self.open(cur["id"])

    def _forget(self) -> None:
        self._session = None

    # -------------------------------------------------------- internals

    def _form_item(self, item_id: str) -> dict[str, Any]:
        self._load()
        item = self.live.item_by_id(item_id)
        if item is None or item.get("type") != FORM_TYPE:
            raise FormError(404, "not_a_form", "No form with that id in the live session")
        return item

    def _apply(self, rec: dict[str, Any]) -> None:
        item_id, op = str(rec.get("item") or ""), rec.get("op")
        if not item_id:
            return
        if op == "open":
            f = self.forms.setdefault(item_id, FormState(str(rec.get("token") or "")))
            f.open = f.opened_ever = True
        elif op == "close" and item_id in self.forms:
            self.forms[item_id].open = False
        elif op == "submit" and item_id in self.forms:
            f = self.forms[item_id]
            f.responses.append({"response_id": rec.get("response_id"), "name": rec.get("name") or "",
                                "answers": rec.get("answers") or {}, "at": rec.get("at")})
            if rec.get("key"):
                f.keys[str(rec["key"])] = str(rec.get("response_id"))

    def _path(self) -> Optional[Path]:
        return self.live.folder / "live" / FORMS_FILE if self.live.folder is not None else None

    def _load(self) -> None:
        """Rebuild the live session's forms by replaying ``forms.jsonl`` (once per session)."""
        if self.live.session_id == self._session:
            return
        self._session = self.live.session_id
        self.forms = {}
        self._log.forget()
        path = self._path()
        if path is None:
            return
        for rec in read_jsonl(path):
            self._apply(rec)
        if self.forms:
            logger.info("✅ forms: %d replayed (%d open)", len(self.forms), sum(f.open for f in self.forms.values()))

    def _record(self, item_id: str, rec: dict[str, Any]) -> None:
        """Apply one record and append it to ``forms.jsonl`` (kept in memory if the write fails)."""
        self._load()
        rec = {"item": item_id, "at": self.clock(), **rec}
        self._apply(rec)
        path = self._path()
        if path is not None:
            self._log.append(path, [rec])

    def _write_csv(self, item: dict[str, Any]) -> None:
        """Rewrite the form's CSV next to the captures, so it is always the full set of responses."""
        if self.live.folder is None:
            return
        path = self.live.folder / "live" / "captures" / f"{item['id']}.csv"
        try:
            atomic_write_text(path, self.csv(item["id"]))
        except (OSError, ValueError) as exc:
            logger.warning("⚠️ form %s: the CSV could not be written (%s) — the responses are in %s",
                           item["id"], exc, FORMS_FILE)
