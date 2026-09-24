"""The activity types the Plan editor can offer (one per ``app/activities/<type>/``)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.webapp.errors import AppError
from src.activities.registry import editors, preview

router = APIRouter()


class PreviewBody(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    answers: Optional[list[str]] = Field(None, max_length=200)


@router.get("/api/activities")
def activity_types() -> dict[str, Any]:
    return {"types": list(editors().values())}


@router.get("/api/geo/suggest")
def geo_suggest(q: str = "") -> dict[str, Any]:
    """Places close to what someone typed (the map's unplaced fix)."""
    from src.geo.gazetteer import gazetteer

    if not q.strip():
        return {"places": []}
    return {"places": [p.as_dict() for p in gazetteer().suggest(q[:120])]}


@router.post("/api/activities/{activity_type}/preview")
def activity_preview(activity_type: str, body: PreviewBody) -> dict[str, Any]:
    """The type's result for its sample answers — the Plan tab's stage preview."""
    if activity_type not in editors():
        raise AppError(404, "unknown_type", f"No activity type {activity_type!r}")
    return {"result": preview(activity_type, body.options, body.answers)}
