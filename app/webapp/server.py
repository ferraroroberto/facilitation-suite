"""FastAPI webapp — the facilitation-suite server on :8449.

Pages (``app/webapp/routers/pages.py``):

    GET /            → the app (Sessions · Plan · Groups · Results tabs, Settings)
    GET /presenter   → the presenter cockpit (second monitor)
    GET /stage       → the stage (full-screen on the display OBS captures)
    WS  /ws          → the live session: state snapshots out, intents in
                       (``app/webapp/routers/live.py``, ``src/live/hub.py``)
    /api/chat/…      → the Zoom chat: the reader process posts here (loopback
                       only), views read it (``src/chat/``)
    /api/quiz/…      → the quiz's join QR code (``app/webapp/routers/quiz.py``)
    GET /healthz     → liveness
    GET /api/version → build identity (git_sha captured at import, schema version)

At startup the lifespan takes the session that was live when the server
stopped live again (``LiveHub.resume_last``, #95; off with ``live.resume_on_start``).

The lifespan also runs the **quiz player listener** — a second, separate app on
``127.0.0.1:<quiz.public_port>`` (``app/player/``), the only surface the
Cloudflare tunnel publishes (``src/tunnel.py``, run by the tray). It shares this
loop and the one ``QuizService``, never this app's routes.

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
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.responses import Response
from starlette.types import Scope

from app.player.listener import PlayerListener
from app.webapp.auth import RemoteAuth, redact_server_logs
from app.webapp.errors import error_response
from app.webapp.routers import (
    actions,
    activities,
    chat,
    groups,
    live,
    pages,
    quiz,
    results,
    sessions,
    settings,
    slides,
)
from src.build_info import build_identity
from src.certs import cert_paths
from src.chat.hub import ChatHub
from src.chat.process import ReaderProcess
from src.config import data_dir, load_config, profiles
from src.errors import DomainError
from src.importer.service import Importer
from src.live.actions import Action, register
from src.live.capture import CaptureService
from src.live.hub import LiveError, LiveHub
from src.logger import configure_logging
from src.music.service import MusicService
from src.music.spotify_login import Login as SpotifyLogin
from src.obs.service import ObsService
from src.quiz.chat import ChatAnswers
from src.quiz.cues import QuizCues
from src.quiz.reach import QuizReach
from src.quiz.service import QUIZ_TYPES, QuizService
from src.sessions.store import SessionStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"
BUILD = build_identity()
LAST_LIVE_FILE = "live.json"  # data/: the session that is live, for the resume at startup


# Bodies under this size go out as they are: gzip framing would cost more than it saves.
GZIP_MIN_BYTES = 1000
# Starlette already leaves PNG/JPEG/WebP/woff2/audio/video alone; these two downloads are zip/PDF inside.
GZIP_EXCLUDED_TYPES = (
    *DEFAULT_EXCLUDED_CONTENT_TYPES,
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


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
    player = PlayerListener(cfg.quiz.public_port, app.state.quiz)
    app.state.player = player
    await player.start()  # optional: a busy port is logged, never fatal
    reach = asyncio.create_task(app.state.quiz_reach.run_periodic(), name="quiz-reach")
    # After every service is wired and the loop bound (timers, the quiz, the music): the session
    # that was live when the server stopped comes back by itself (#95).
    if cfg.live.resume_on_start:
        app.state.live.resume_last()
    else:
        logger.info("ℹ️ live: resume at startup is off (config live.resume_on_start) — go live by hand")
    logger.info("✅ facilitation-suite up — build %s · port %d · config %s", BUILD["git_sha"], cfg.port, cfg.source)
    yield
    reach.cancel()
    await player.stop()
    monitor.cancel()
    app.state.obs.stop()
    await asyncio.to_thread(app.state.music.close)
    await asyncio.to_thread(app.state.reader_process.stop)
    logger.info("👋 facilitation-suite stopping")


def _install_error_handlers(app: FastAPI) -> None:
    # Any DomainError (AppError and every src/ module's error) → the one JSON envelope.
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> Response:
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


def _install_music(app: FastAPI) -> None:
    """Music on this PC's audio output, synced to item timers (``src/music/``)."""
    app.state.music = MusicService(app.state.live)
    # Settings → Music → Connect Spotify: the same PKCE login as scripts/spotify_login.py (#110).
    app.state.spotify_login = SpotifyLogin(on_saved=app.state.music.spotify_login_saved)


def _install_quiz(app: FastAPI) -> None:
    """The live quiz game: players, phases, answers and scores (``src/quiz/service.py``), its
    Zoom-chat fallback (``src/quiz/chat.py``; own messages count only in the capture's rehearsal mode)
    and the public-link check (``src/quiz/reach.py``: when a quiz session goes live, then every few
    minutes while it is, and on demand — always in a worker thread)."""
    service = QuizService(app.state.live)
    service.public_url = lambda: app.state.config.quiz.public_url
    service.listener_up = lambda: bool(getattr(app.state, "player", None) and app.state.player.running)
    service.output_device = app.state.music.output_device  # the Sounds chip's device state (#102)
    app.state.quiz = service
    live = app.state.live

    def quiz_live() -> bool:  # the live session plays a quiz: the public link is checked every few minutes
        return live.session_id is not None and any(it.get("type") in QUIZ_TYPES for it in live.items)

    def pushed() -> None:  # called from the checking thread
        if live.loop is not None:
            live.loop.call_soon_threadsafe(live.push_state)

    reach = QuizReach(lambda: (app.state.config.quiz.public_url, app.state.config.quiz.public_port),
                      wanted=quiz_live, on_change=pushed)
    service.reach = reach.current
    live.session_listeners.append(lambda sid: reach.kick() if sid and quiz_live() else None)
    app.state.quiz_reach = reach
    app.state.quiz_chat = ChatAnswers(service, app.state.chat, count_own=lambda: app.state.capture.count_own)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="facilitation-suite", version="0.1.0", lifespan=_lifespan)
    # Gzip the pages, JS/CSS and JSON for a phone on cellular (#166). Registered first so it is the
    # innermost middleware: the ``@app.middleware("http")`` in ``_install_chat`` re-streams every body,
    # and a gzip outside it would ignore the minimum size and compress even the bodyless 304.
    # WebSocket scopes pass through untouched; Starlette skips ``text/event-stream``.
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES, compresslevel=6, exclude_content_types=GZIP_EXCLUDED_TYPES)
    app.state.config = load_config()
    app.state.build = BUILD
    store = SessionStore(app.state.config)
    app.state.store = store
    app.state.importer = Importer(load=store.load, save=store.save, folder=store.folder)
    app.state.live = LiveHub(store, last_live=data_dir() / LAST_LIVE_FILE)
    app.state.last_action = None  # the latest /api/actions press (the presenter's Stream Deck chip)
    app.state.live.extra_state.append(lambda: {"last_action": app.state.last_action})
    # Light/dark for the app, the presenter and the remote (#92): every open page follows it.
    app.state.live.extra_state.append(lambda: {"appearance": app.state.config.appearance})
    # What a click on the stage does (#191): the stage window reads it from the snapshot.
    app.state.live.extra_state.append(lambda: {"stage_click": settings.stage_click_state(app.state.config)})
    _install_chat(app)
    _install_obs(app)
    _install_music(app)
    _install_quiz(app)
    # The quiz's phase sounds through the music service (after both: it hears the quiz's changes).
    app.state.quiz_cues = QuizCues(app.state.live, app.state.quiz, app.state.music)
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
    app.include_router(quiz.router)
    return app


# Module-level app for ``uvicorn app.webapp.server:app``.
app = create_app()
