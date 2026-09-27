"""FastAPI webapp — the facilitation-suite server on :8449.

Pages (``app/webapp/routers/pages.py``):

    GET /            → the app (Sessions · Plan · Groups · Results tabs, Settings)
    GET /presenter   → the presenter cockpit (second monitor)
    GET /stage       → the stage (full-screen on the display OBS captures)
    WS  /ws          → the live session: state snapshots out, intents in
                       (``app/webapp/routers/live.py``, ``src/live/hub.py``)
    /api/chat/…      → the Zoom chat: the reader process posts here (loopback
                       only), views read it (``src/chat/``)
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

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.webapp.auth import RemoteAuth, redact_server_logs
from app.webapp.errors import AppError, error_response
from app.webapp.routers import (
    actions,
    activities,
    chat,
    groups,
    live,
    pages,
    results,
    sessions,
    settings,
    slides,
)
from src.build_info import build_identity
from src.certs import cert_paths
from src.chat.hub import ChatHub
from src.chat.process import ReaderProcess
from src.config import load_config, profiles
from src.importer.service import Importer
from src.live.actions import Action, register
from src.live.capture import CaptureService
from src.live.hub import LiveError, LiveHub
from src.logger import configure_logging
from src.obs.service import ObsService
from src.sessions.store import SessionStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"
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
    app.state.live.bind(asyncio.get_running_loop())
    monitor = asyncio.create_task(app.state.chat.monitor())
    app.state.obs.start()
    logger.info("✅ facilitation-suite up — build %s · port %d · config %s", BUILD["git_sha"], cfg.port, cfg.source)
    yield
    monitor.cancel()
    app.state.obs.stop()
    await asyncio.to_thread(app.state.reader_process.stop)
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


def _install_chat(app: FastAPI) -> None:
    """The chat hub and its reader process: the reader runs while a session is
    live (when the config enables it) and can be started by hand for a test."""
    cfg = app.state.config
    live = app.state.live
    chat_hub = ChatHub(live)
    scheme = "https" if cert_paths() else "http"  # the reader falls back to the other scheme itself
    proc = ReaderProcess(f"{scheme}://127.0.0.1:{cfg.port}")

    capture = CaptureService(live, chat_hub)
    capture.freeze_url = f"{scheme}://127.0.0.1:{cfg.port}"
    app.state.capture = capture

    @app.middleware("http")
    async def _note_server_address(request: Request, call_next):  # noqa: ANN202 — Starlette middleware signature
        # The reader and the freeze renderer must reach the port this server
        # really listens on (a dev or test instance can differ from the config).
        server = request.scope.get("server")
        if server and server[1]:
            proc.server_url = capture.freeze_url = f"{request.url.scheme}://127.0.0.1:{server[1]}"
        return await call_next(request)
    chat_hub.process_running = proc.running
    app.state.chat = chat_hub
    app.state.reader = chat_hub  # readiness facts (sessions router)
    app.state.reader_process = proc

    def on_session(sid: Optional[str]) -> None:
        chat_hub.sync_session()
        if sid and cfg.reader.enabled and not proc.running():
            proc.start()
        elif sid is None and proc.running() and live.loop is not None:
            live.loop.run_in_executor(None, proc.stop)

    live.session_listeners.append(on_session)


def _install_obs(app: FastAPI) -> None:
    """OBS follows each item's profile; its state rides on every live snapshot."""
    live = app.state.live

    def pushed() -> None:  # called from the OBS worker thread
        if live.loop is not None:
            live.loop.call_soon_threadsafe(live.push_state)

    obs = ObsService(lambda: app.state.config, on_change=pushed)
    app.state.obs = obs
    live.zones = lambda: {k: v["zone"] for k, v in profiles(app.state.config).items()}
    live.item_listeners.append(lambda prev, cur: obs.switch(cur.get("profile")))
    live.session_listeners.append(lambda sid: obs.switch((live.current() or {}).get("profile")) if sid else None)
    live.extra_state.append(lambda: {"obs": obs.snapshot()})

    def by_hand(hub: LiveHub, name: Optional[str]) -> None:
        if name not in profiles(app.state.config):
            raise LiveError(404, "unknown_profile", f"No OBS profile {name!r} (camera_strip, camera_pip, screen_only)")
        obs.switch(name)

    register(Action("obs_profile", "Switch the OBS profile", by_hand, arg="name"))


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="facilitation-suite", version="0.1.0", lifespan=_lifespan)
    app.state.config = load_config()
    app.state.build = BUILD
    store = SessionStore(app.state.config)
    app.state.store = store
    app.state.importer = Importer(load=store.load, save=store.save, folder=store.folder)
    app.state.live = LiveHub(store)
    app.state.last_action = None  # the latest /api/actions press (the presenter's Stream Deck chip)
    app.state.live.extra_state.append(lambda: {"last_action": app.state.last_action})
    _install_chat(app)
    _install_obs(app)
    _install_error_handlers(app)
    # Outermost: other devices need the phone-remote token before anything else runs.
    app.add_middleware(RemoteAuth, get_token=lambda: app.state.config.remote.token)
    redact_server_logs()
    app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")
    # Stage themes (public, repo-level): the stage follows the session theme, not the fleet design.
    app.mount("/themes", NoCacheStaticFiles(directory=str(THEMES_DIR)), name="themes")
    app.include_router(pages.router)
    app.include_router(sessions.router)
    app.include_router(slides.router)
    app.include_router(activities.router)
    app.include_router(live.router)
    app.include_router(chat.router)
    app.include_router(groups.router)
    app.include_router(results.router)
    app.include_router(settings.router)
    app.include_router(actions.router)
    return app


# Module-level app for ``uvicorn app.webapp.server:app``.
app = create_app()
