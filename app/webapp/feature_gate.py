"""A route guard for a feature block: its API answers one plain sentence while the block is switched off."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request

from app.webapp.errors import AppError
from src import features


def require(key: str) -> Callable[[Request], None]:
    """A FastAPI dependency that refuses with 404 while block ``key`` is off.

    404, not 403: the block is not part of this machine's suite, so a caller sees the route as absent. The loaded
    config is read on every request, so a switch in Settings applies at once."""
    if key not in features.DEFAULTS:
        raise KeyError(key)

    def guard(request: Request) -> None:
        if not features.enabled(request.app.state.config, key):
            raise AppError(404, "feature_off", features.OFF_SENTENCE)

    return guard


def remote_token(cfg: Any) -> str:
    """The phone remote's token, or "" while the remote block is switched off (then no other device gets in)."""
    return cfg.remote.token if features.enabled(cfg, "remote") else ""
