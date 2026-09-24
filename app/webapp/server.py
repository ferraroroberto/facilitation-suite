"""FastAPI webapp — the facilitation-suite server on :8449.

Pages (``app/webapp/routers/pages.py``):

    GET /            → the app (Sessions · Plan · Groups · Results tabs, Settings)
    GET /presenter   → the presenter cockpit (second monitor)
    GET /stage       → the stage (full-screen on the display OBS captures)
    GET /healthz     → liveness
    GET /api/version → build identity (git_sha captured at import, schema version)

Static assets are served ``no-cache`` (revalidated by ETag on every load):
the app runs on this PC and on a phone over the tailnet, so a stale asset
after a restart is the only real risk, and revalidation removes it without a
hash-stamping build step.

Errors are one JSON envelope everywhere — ``{"error": {"code", "message",
"detail"?}}``.

Run under uvicorn with the pinned selector loop (Windows proactor wedges on an
aborted client — app-launcher#388):

    python -m uvicorn app.webapp.server:app --host 0.0.0.0 --port 8449 \
        --loop app.webapp.event_loop:selector_loop_factory
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.webapp.errors import AppError, error_response
from app.webapp.routers import pages
from src.build_info import build_identity
from src.config import load_config
from src.logger import configure_logging

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
BUILD = build_identity()


class NoCacheStaticFiles(StaticFiles):
    """``StaticFiles`` that always revalidates (``no-cache`` + the ETag)."""

    def file_response(
        self,
        full_path: os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = app.state.config
    logger.info("✅ facilitation-suite up — build %s · port %d · config %s", BUILD["git_sha"], cfg.port, cfg.source)
    yield
    logger.info("👋 facilitation-suite stopping")


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> Response:
        return error_response(exc.status, exc.code, str(exc), exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        return error_response(422, "validation_error", "invalid request", exc.errors())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error_response(exc.status_code, code, str(exc.detail))


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="facilitation-suite", version="0.1.0", lifespan=_lifespan)
    app.state.config = load_config()
    app.state.build = BUILD
    _install_error_handlers(app)
    app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(pages.router)
    return app


# Module-level app for ``uvicorn app.webapp.server:app``.
app = create_app()
