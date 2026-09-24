"""``session.yaml`` — the plan of one session (schema v1).

Every model allows unknown keys and dumps them back unchanged, so a file
edited by hand (or by a newer version of the app) survives a load → save
round-trip. A ``schema`` bump gets an entry in ``MIGRATIONS``.

Timers are per item only (epic §9): an item has no timer until one is added,
and nothing is inherited from sections or settings.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.activities.registry import editors

SCHEMA_VERSION = 1

Profile = Literal["camera_strip", "camera_pip", "screen_only"]
PROFILES: tuple[str, ...] = ("camera_strip", "camera_pip", "screen_only")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Timer(_Open):
    enabled: bool = True
    seconds: int = Field(180, ge=5, le=6 * 3600)
    start: Literal["manual", "on_enter", "with_capture"] = "manual"
    show_on: Literal["stage", "presenter", "both"] = "both"
    end: Literal["keep", "stop_capture", "advance", "chime"] = "keep"


class Font(_Open):
    """``size_px`` is in stage pixels: the stage is a 1920×1080 canvas scaled to its window."""

    family: str = "Patrick Hand"
    size_px: int = Field(72, ge=12, le=240)


class Item(_Open):
    """A slide, an activity or a break, in plan order."""

    kind: Literal["slide", "activity", "break"]
    id: str = ""
    title: str = ""
    profile: Optional[Profile] = None
    include: bool = True
    timer: Optional[Timer] = None
    # slides
    slide_id: Optional[int] = None
    # activities
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
    source: Optional[Source] = None
    # Manual readiness confirmations (e.g. zoom_autoupdate_off) the app cannot detect itself.
    checklist: dict[str, bool] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)

    def all_items(self) -> list[Item]:
        return [it for s in self.sections for it in s.items]


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:24] or "x"


def ensure_ids(session: Session) -> Session:
    """Give every section and item a stable, unique id (slides: ``slide-<SlideID>``)."""
    seen: set[str] = set()
    for sec in session.sections:
        if not sec.id or sec.id in seen:
            sec.id = f"sec-{_slug(sec.name)}"
            while sec.id in seen:
                sec.id = new_id("sec")
        seen.add(sec.id)
        for it in sec.items:
            if not it.id or it.id in seen:
                if it.kind == "slide" and it.slide_id is not None and f"slide-{it.slide_id}" not in seen:
                    it.id = f"slide-{it.slide_id}"
                else:
                    it.id = new_id({"slide": "slide", "activity": "act", "break": "brk"}[it.kind])
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


_EMPTY_ITEM_KEYS = ("question", "chat_prompt", "options", "title")


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
    return data
