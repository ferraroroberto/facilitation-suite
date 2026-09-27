"""The chat: the reader posts here (loopback only); views read and control it."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.webapp.errors import require_local
from src.chat.hub import ChatHub
from src.chat.process import ReaderProcess

router = APIRouter(prefix="/api/chat")


def _chat(request: Request) -> ChatHub:
    return request.app.state.chat


def _proc(request: Request) -> ReaderProcess:
    return request.app.state.reader_process


class Row(BaseModel):
    sender: str = Field(max_length=200)
    text: str = Field(max_length=4000)
    time: str = Field("", max_length=16)
    received_at: Optional[int] = None


class Batch(BaseModel):
    batch: str = Field("", max_length=64)
    baseline: bool = False
    source: Literal["zoom", "simulator"] = "zoom"
    messages: list[Row] = Field(default_factory=list, max_length=5000)


class Beat(BaseModel):
    model_config = {"extra": "allow"}
    state: str = Field(max_length=40)
    detail: str = Field("", max_length=400)


class Simulate(BaseModel):
    kind: Literal["random", "burst", "list"] = "random"
    count: int = Field(30, ge=1, le=500)
    every_ms: int = Field(700, ge=50, le=10000)
    answers: list[str] = Field(default_factory=list, max_length=200)


@router.post("/messages")
async def post_messages(request: Request, body: Batch) -> dict[str, Any]:
    require_local(request, "Only the chat reader on this PC can post messages")
    stored = _chat(request).ingest(body.batch, [r.model_dump() for r in body.messages],
                                   baseline=body.baseline, source=body.source)
    return {"stored": len(stored)}


@router.post("/heartbeat")
async def heartbeat(request: Request, body: Beat) -> dict[str, bool]:
    require_local(request, "Only the chat reader on this PC can report its state")
    _chat(request).heartbeat(body.model_dump())
    return {"ok": True}


@router.get("/messages")
async def get_messages(request: Request, since: int = 0) -> dict[str, Any]:
    chat = _chat(request)
    msgs = chat.since(max(0, since))
    return {"messages": msgs, "count": len(chat.messages), "reader": chat.state_fields()["reader"]}


@router.post("/reader/start")
async def reader_start(request: Request) -> dict[str, Any]:
    mode = await asyncio.to_thread(_proc(request).start)
    _chat(request).refresh()
    return {"running": mode}


@router.post("/reader/stop")
async def reader_stop(request: Request) -> dict[str, Any]:
    await asyncio.to_thread(_proc(request).stop)
    _chat(request).refresh()
    return {"running": None}


@router.post("/simulate")
async def simulate(request: Request, body: Simulate) -> dict[str, Any]:
    """Rehearse alone: replay fake answers through the same POST as the reader."""
    spec = {"burst": f"burst:{body.count}", "list": f"list:{body.every_ms}"}.get(body.kind, f"random:{body.count}:{body.every_ms}")
    answers = [a for a in body.answers if a.strip()] or None
    await asyncio.to_thread(_proc(request).start, spec, answers)
    _chat(request).refresh()
    return {"running": "simulator", "script": spec}
