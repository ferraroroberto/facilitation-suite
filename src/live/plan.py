"""The run order of a live session: the plan flattened for the stage and presenter.

Items switched off ("In this session" off) are left out — they stay in the
plan but are skipped live. Every run item carries what the stage and the
presenter need to render it without another lookup: the display title, the
slide file and notes, the section it belongs to and that section's planned
start (minutes from the session start), and the per-item timer.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.activities.registry import editors
from src.config import DEFAULT_PROFILES
from src.sessions.model import THEME_FONT, Session

# Camera zones as fractions of the 1920×1080 canvas (x0, y0, x1, y1): the
# defaults; Settings can move them (config "profiles", passed in as ``zones``).
ZONES: dict[str, Optional[list[float]]] = {k: v["zone"] for k, v in DEFAULT_PROFILES.items()}


def _slide_index(meta: Optional[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(s["slide_id"]): s for s in (meta or {}).get("slides") or [] if "slide_id" in s}


_BREAKS = re.compile(r"\s*(?:\\n|\r?\n)\s*")


def one_line(text: str) -> str:
    """A title on one line: ``\\n`` (typed as backslash-n) breaks it only on the stage."""
    return _BREAKS.sub(" ", text or "").strip()


# The stage's default titles in each session language (app/webapp/static/js/stage-words.js
# says the same words on the stage; tests/test_live.py keeps them in step).
DEFAULT_TITLES: dict[str, dict[str, str]] = {
    "en": {"break": "Break", "breakout": "Breakout rooms", "groups_reveal": "Who are you with?"},
    "es": {"break": "Descanso", "breakout": "Salas de grupos", "groups_reveal": "¿Con quién estás?"},
}


def display_title(item: Any, slide: Optional[dict[str, Any]], types: dict[str, Any], lang: str = "en") -> str:
    words = DEFAULT_TITLES.get(lang, DEFAULT_TITLES["en"])
    if item.title.strip():
        return item.title.strip()
    if item.kind == "slide":
        return (slide or {}).get("title") or f"Slide {item.slide_id}"
    if item.kind in ("break", "breakout"):
        return words[item.kind]
    if item.question.strip():
        return item.question.strip()
    return words.get(item.type or "") or (types.get(item.type or "") or {}).get("label") or "Activity"


def _slide_text(item: Any, slide: Optional[dict[str, Any]]) -> dict[str, Any]:
    """A slide whose text the stage draws itself: its text-free picture and text boxes."""
    if item.kind != "slide" or item.live_text is False or not slide or not slide.get("bg_file") or not slide.get("boxes"):
        return {}
    return {"slide_bg": slide["bg_file"], "text_boxes": slide["boxes"]}


def build_run(session: Session, meta: Optional[dict[str, Any]], rounds: Optional[dict[str, Any]] = None,
              zones: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """``{"items": [...], "sections": [...]}`` in live order (included items only).

    ``rounds`` (from groups.yaml) gives each "who are you with?" item its rooms.
    """
    slides = _slide_index(meta)
    types = editors()
    zones = zones or ZONES
    items: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    start = 0
    for sec in session.sections:
        run_items = [it for it in sec.items if it.include]
        sections.append({
            "id": sec.id, "name": sec.name, "minutes": sec.minutes, "planned_start": start,
            "has_break": any(it.kind == "break" for it in run_items),
            "first_index": len(items) if run_items else None,
        })
        for it in run_items:
            slide = slides.get(it.slide_id) if it.kind == "slide" and it.slide_id is not None else None
            profile = it.profile or (slide or {}).get("profile") or "screen_only"
            spec = types.get(it.type or "") or {}
            items.append({
                "index": len(items),
                "id": it.id,
                "kind": it.kind,
                "type": it.type,
                "type_label": spec.get("label"),
                "capture": bool(spec.get("capture", False)) if it.kind == "activity" else False,
                "title": display_title(it, slide, types, session.language),
                "question": it.question,
                "chat_prompt": it.chat_prompt,
                "font": (it.font.model_dump() if it.font else {"family": THEME_FONT, "size_px": 72}),
                "options": it.options,
                "notes": it.notes or ((slide or {}).get("notes", "") if it.kind == "slide" else ""),
                "notes_own": bool(it.notes),
                "slide_file": (slide or {}).get("file") if slide else None,
                **_slide_text(it, slide),
                "slide_missing": it.kind == "slide" and slide is None,
                "profile": profile,
                "zone": zones.get(profile),
                "timer": it.timer.model_dump() if it.timer and it.timer.enabled else None,
                "music": it.music.model_dump() if it.music and it.music.enabled else None,
                "section_id": sec.id,
                "section_name": sec.name,
                **({"rooms": (rounds or {}).get((it.options or {}).get("round", "pairs")) or []}
                   if it.type == "groups_reveal" else {}),
                **({"rooms": (rounds or {}).get((it.options or {}).get("round") or "") or []}
                   if it.kind == "breakout" else {}),
            })
        start += sec.minutes
    return {"items": items, "sections": sections, "planned_minutes": start,
            "chat_hint": session.chat_hint, "language": session.language, "theme": session.theme}
