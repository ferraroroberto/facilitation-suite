"""``session.yaml`` — the plan of one session (schema v1).

Every model allows unknown keys and dumps them back unchanged, so a file
edited by hand (or by a newer version of the app) survives a load → save
round-trip. A ``schema`` bump gets an entry in ``MIGRATIONS``.

Timers are per item only (epic §9): an item has no timer until one is added,
and nothing is inherited from sections or settings.
"""

from __future__ import annotations

import logging
import re
import secrets
import unicodedata
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.activities.registry import editors

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# An item id — the one rule for every route that takes one. It names the item's
# files (``live/captures/<id>.json``/``.png``), so no dots, slashes or spaces.
# Every id the app generates fits (``slide-<SlideID>``, ``act-``/``brk-``/``bko-``
# + hex); so does a hand-written one like ``act-Q1``. Use ``fullmatch``.
ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
# Windows reserved device names: a file named exactly this (any case, whatever the
# extension) cannot be created on Windows — ``live/captures/con.json`` would fail.
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul"} |
                               {f"com{d}" for d in "123456789"} | {f"lpt{d}" for d in "123456789"})

Profile = Literal["camera_strip", "camera_pip", "screen_only"]
Language = Literal["en", "es"]
# The stage hint's old stored default: it now comes from the session's language.
LEGACY_CHAT_HINT = "Write your answer in the Zoom chat"
PROFILES: tuple[str, ...] = ("camera_strip", "camera_pip", "screen_only")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Timer(_Open):
    enabled: bool = True
    seconds: int = Field(180, ge=5, le=6 * 3600)
    start: Literal["manual", "on_enter", "with_capture"] = "manual"
    show_on: Literal["stage", "presenter", "both"] = "both"
    # At 00:00: keep showing it, stop the capture, go to the next item, chime, or take it off the stage.
    end: Literal["keep", "stop_capture", "advance", "chime", "hide"] = "keep"


class Music(_Open):
    """The music an item plays (``src/music/``): a file in the session folder
    (``path``, session-relative, usually ``audio/<name>``) or a Spotify
    playlist, album or track (``uri``: a ``spotify:`` URI or an
    ``open.spotify.com`` link). It starts with the item's timer, when the item
    opens, or only by hand from the presenter; pausing the timer fades it out
    and pauses it, a reset or 00:00 fades it out and stops it, and leaving the
    item fades it out or keeps it playing."""

    enabled: bool = True
    source: Literal["file", "spotify"] = "file"
    path: str = ""
    uri: str = ""
    volume: int = Field(80, ge=0, le=100)
    fade_in_s: float = Field(2.0, ge=0, le=60)
    fade_out_s: float = Field(2.0, ge=0, le=60)
    loop: bool = False  # files: start over at the end
    start: Literal["with_timer", "on_enter", "manual"] = "with_timer"
    on_leave: Literal["fade_out", "keep_playing"] = "fade_out"


THEME_FONT = "theme"

# The kinds of text on the stage, each with its own font and capitals: titles
# and questions; subtitles (a breakout's round); the chat hint ("Write your
# answer in the chat"); the answers (word cloud, cards, feed, scale, map,
# groups); and a slide's text other than its title.
TEXT_ROLES: tuple[str, ...] = ("title", "sub", "hint", "answers", "slide_text")


class TextRole(_Open):
    """Where one kind of stage text takes its lettering from. ``font`` is
    ``"title"`` or ``"text"`` (the session's two fonts), a font installed on
    this PC, or ``""`` to follow (the session, then the default: the title font
    for titles, the text font for the rest); ``caps`` ``None`` follows too."""

    font: str = ""
    caps: Optional[bool] = None


def _known_roles(v: Any) -> Any:
    """Only the stage's kinds of text; an unknown key (a typo) is dropped."""
    return {k: r for k, r in v.items() if k in TEXT_ROLES} if isinstance(v, dict) else v


class Font(_Open):
    """One item's lettering, an exception to the session's (``StageFont``).
    ``family`` is ``"theme"`` (the session's stage font) or a font installed on
    this PC; ``size_px`` is in stage pixels (the stage is a 1920×1080 canvas
    scaled to its window); ``caps`` — capitals or as typed — is ``None`` to
    follow the session. Those three are the item's title; ``roles`` sets its
    other text (``TEXT_ROLES`` but ``title``) apart from the session's."""

    family: str = THEME_FONT
    size_px: int = Field(72, ge=12, le=240)
    caps: Optional[bool] = None
    roles: dict[str, TextRole] = Field(default_factory=dict)

    _roles = field_validator("roles", mode="before")(classmethod(lambda cls, v: _known_roles(v)))

    @field_validator("family")
    @classmethod
    def _theme_alias(cls, v: str) -> str:
        # Before the session font existed the editor offered "Patrick Hand" as
        # "the session theme": it meant the theme's font, so it still does.
        return THEME_FONT if v.strip() in ("", "Patrick Hand") else v


class StageFont(_Open):
    """The stage's lettering for the whole session — every item follows it unless
    it sets its own (``Item.font``): a font file on this PC
    (``.otf``/``.ttf``/``.woff``/``.woff2``), else a font installed on this PC
    (``family``; both empty = the theme's Patrick Hand); the weight; extra line
    thickness in stage px, for thin handwriting fonts; and whether questions
    and titles are in capitals. That is the **title font**. The **text font**
    (``text_family``, ``text_weight``; empty = the chat hint's plain sans) is
    for every other text. ``roles`` says which of the two each kind of text
    uses, and its capitals (a title's capitals are ``caps``)."""

    file: str = ""
    family: str = ""
    weight: Literal[400, 700] = 400
    stroke_px: float = Field(0, ge=0, le=8)
    caps: bool = True
    text_family: str = ""
    text_weight: Literal[400, 700] = 400
    roles: dict[str, TextRole] = Field(default_factory=dict)

    _roles = field_validator("roles", mode="before")(classmethod(lambda cls, v: _known_roles(v)))


class Item(_Open):
    """A slide, an activity, a break or a breakout (rooms, with their clock), in plan order."""

    kind: Literal["slide", "activity", "break", "breakout"]
    id: str = ""
    title: str = ""
    profile: Optional[Profile] = None
    include: bool = True
    timer: Optional[Timer] = None
    music: Optional[Music] = None
    # The presenter's notes for this item; on a slide they replace its PowerPoint notes.
    notes: str = ""
    # slides
    slide_id: Optional[int] = None
    # A slide's text drawn by the stage in the session's font (None = yes, when
    # the import found plain text on it); False keeps PowerPoint's picture.
    live_text: Optional[bool] = None
    # activities (and a breakout's round: options.round)
    type: Optional[str] = None
    question: str = ""
    font: Optional[Font] = None
    chat_prompt: str = ""
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: Optional[str]) -> Optional[str]:
        known = editors()
        if v is not None and v not in known:
            raise ValueError(f"unknown activity type {v!r} (known: {', '.join(known)})")
        return v


class Section(_Open):
    id: str = ""
    name: str = "Section"
    minutes: int = Field(10, ge=0, le=24 * 60)
    items: list[Item] = Field(default_factory=list)


class Source(_Open):
    pptx: str = ""
    imported_at: Optional[datetime] = None


class Session(_Open):
    schema_: int = Field(SCHEMA_VERSION, alias="schema")
    title: str = "Untitled session"
    date: Optional[datetime] = None
    duration_minutes: int = Field(120, ge=1, le=24 * 60)
    theme: str = "default"
    # The words the stage says by itself (the chat hint, default titles).
    language: Language = "en"
    # The chip on the stage under every activity; empty = the language's own.
    chat_hint: str = ""
    font: Optional[StageFont] = None
    source: Optional[Source] = None
    # Manual readiness confirmations (e.g. zoom_autoupdate_off) the app cannot detect itself.
    checklist: dict[str, bool] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)

    @field_validator("chat_hint")
    @classmethod
    def _hint_default(cls, v: str) -> str:
        return "" if v.strip() == LEGACY_CHAT_HINT else v

    def all_items(self) -> list[Item]:
        return [it for s in self.sections for it in s.items]


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:24] or "x"


def _valid_item_id(item_id: str) -> str:
    """A hand-edited id outside ``ITEM_ID`` (or a Windows reserved device name —
    ``con``/``prn``/``aux``/``nul``/``com1``-``com9``/``lpt1``-``lpt9``, any case)
    made fit, the same way on every load (``"act Q1"`` → ``"act-Q1"``,
    ``"con"`` → ``"con-x"``); ``""`` when nothing is left of it."""
    if not item_id:
        return item_id
    if ITEM_ID.fullmatch(item_id) and item_id.lower() not in _WINDOWS_RESERVED:
        return item_id
    ascii_id = unicodedata.normalize("NFKD", item_id).encode("ascii", "ignore").decode("ascii")
    fixed = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_id).strip("-")[:80]
    if fixed.lower() in _WINDOWS_RESERVED:
        fixed = f"{fixed}-x"
    logger.warning("⚠️ session plan: item id %r is not valid — using %r until the plan is saved",
                   item_id, fixed or "a new id")
    return fixed


def ensure_ids(session: Session) -> Session:
    """Give every section and item a stable, unique id (slides: ``slide-<SlideID>``);
    an item id outside ``ITEM_ID`` is made to fit."""
    seen: set[str] = set()
    for sec in session.sections:
        if not sec.id or sec.id in seen:
            sec.id = f"sec-{_slug(sec.name)}"
            while sec.id in seen:
                sec.id = new_id("sec")
        seen.add(sec.id)
        for it in sec.items:
            it.id = _valid_item_id(it.id)
            if not it.id or it.id in seen:
                if it.kind == "slide" and it.slide_id is not None and f"slide-{it.slide_id}" not in seen:
                    it.id = f"slide-{it.slide_id}"
                else:
                    it.id = new_id({"slide": "slide", "activity": "act", "break": "brk", "breakout": "bko"}[it.kind])
                while it.id in seen:
                    it.id = new_id(it.kind[:3])
            seen.add(it.id)
    return session


# schema version → function upgrading the raw dict from (version - 1).
MIGRATIONS: dict[int, Any] = {}


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    version = int(raw.get("schema", 1) or 1)
    if version > SCHEMA_VERSION:
        raise ValueError(f"session.yaml schema {version} is newer than this app ({SCHEMA_VERSION})")
    while version < SCHEMA_VERSION:
        version += 1
        raw = MIGRATIONS[version](raw)
        raw["schema"] = version
    return raw


def parse_session(raw: Any) -> Session:
    if not isinstance(raw, dict):
        raise ValueError("session.yaml must be a mapping")
    return ensure_ids(Session.model_validate(migrate(dict(raw))))


_EMPTY_ITEM_KEYS = ("question", "chat_prompt", "options", "title", "notes")


def dump_session(session: Session) -> dict[str, Any]:
    """Plain data for YAML: aliases (``schema``), extras kept, and no noise —
    ``None`` and the empty activity fields are left out so a slide item stays
    a few readable lines."""
    data = session.model_dump(mode="json", by_alias=True, exclude_none=True)
    for sec in data.get("sections", []):
        for it in sec.get("items", []):
            for key in _EMPTY_ITEM_KEYS:
                if it.get(key) in ("", {}):
                    it.pop(key)
            if it.get("include") is True:
                it.pop("include")
            _quiet_roles(it.get("font"))
    _quiet_roles(data.get("font"))
    return data


def _quiet_roles(font: Any) -> None:
    """A lettering block's kinds of text without what only follows (an empty font, no exceptions at all)."""
    if not isinstance(font, dict):
        return
    for style in (font.get("roles") or {}).values():
        if style.get("font") == "":
            style.pop("font")
    if not font.get("roles"):
        font.pop("roles", None)
