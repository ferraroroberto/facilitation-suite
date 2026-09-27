"""The run order of a live session: the plan flattened for the stage and presenter.

Items switched off ("In this session" off) are left out — they stay in the
plan but are skipped live. Every run item carries what the stage and the
presenter need to render it without another lookup: the display title, the
slide file and notes, the section it belongs to and that section's planned
start (minutes from the session start), and the per-item timer.
"""

from __future__ import annotations

from typing import Any, Optional

from src.activities.registry import editors
from src.sessions.model import Session

# Camera zones as fractions of the 1920×1080 canvas (x0, y0, x1, y1). The strip
# box matches the grey area of the house slides; content avoids the whole strip.
ZONES: dict[str, Optional[list[float]]] = {
    "camera_strip": [0.583, 0.23, 0.983, 0.77],
    "camera_pip": [0.72, 0.04, 0.98, 0.3],
    "screen_only": None,
}


def _slide_index(meta: Optional[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(s["slide_id"]): s for s in (meta or {}).get("slides") or [] if "slide_id" in s}


def display_title(item: Any, slide: Optional[dict[str, Any]], types: dict[str, Any]) -> str:
    if item.title.strip():
        return item.title.strip()
    if item.kind == "slide":
        return (slide or {}).get("title") or f"Slide {item.slide_id}"
    if item.kind == "break":
        return "Break"
    if item.question.strip():
        return item.question.strip()
    return (types.get(item.type or "") or {}).get("label") or "Activity"


def build_run(session: Session, meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    """``{"items": [...], "sections": [...]}`` in live order (included items only)."""
    slides = _slide_index(meta)
    types = editors()
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
                "title": display_title(it, slide, types),
                "question": it.question,
                "chat_prompt": it.chat_prompt,
                "font": (it.font.model_dump() if it.font else {"family": "Patrick Hand", "size_px": 72}),
                "options": it.options,
                "notes": (slide or {}).get("notes", "") if it.kind == "slide" else "",
                "slide_file": (slide or {}).get("file") if slide else None,
                "slide_missing": it.kind == "slide" and slide is None,
                "profile": profile,
                "zone": ZONES.get(profile),
                "timer": it.timer.model_dump() if it.timer and it.timer.enabled else None,
                "section_id": sec.id,
                "section_name": sec.name,
            })
        start += sec.minutes
    return {"items": items, "sections": sections, "planned_minutes": start,
            "chat_hint": session.chat_hint, "theme": session.theme}
