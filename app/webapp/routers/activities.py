"""The activity types the Plan editor can offer (one per ``app/activities/<type>/``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.activities.registry import editors

router = APIRouter()


@router.get("/api/activities")
def activity_types() -> dict[str, Any]:
    return {"types": list(editors().values())}
