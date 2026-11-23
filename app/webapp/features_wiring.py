"""The feature blocks (``src/features.py``) wired into the running server.

Every service asks the live hub's ``feature_on`` before it does a block's work, the live snapshot carries the blocks
switched off (every page hides their parts), and ``apply`` stops a block's running work the moment Settings switches
it off — or starts what only runs while it is on (the quiz player's public listener).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from app.webapp.route_pools import PROCESSES, run_on
from src import features

logger = logging.getLogger(__name__)


def install(app: FastAPI) -> None:
    """Point the live hub at the loaded config's switches and give Settings its ``apply_features``."""
    live = app.state.live
    live.feature_on = lambda key: features.enabled(app.state.config, key)
    live.extra_state.append(lambda: {"features_off": features.off_keys(app.state.config)})
    app.state.apply_features = _applier(app)


def _applier(app: FastAPI) -> Callable[[], Awaitable[None]]:
    async def apply() -> None:
        on = app.state.live.feature_on
        if not on("chat_reader") and app.state.reader_process.running():
            await run_on(PROCESSES, app.state.reader_process.stop)
            app.state.chat.refresh()
        app.state.obs.reconnect()  # its worker reads the switch again: off disconnects, on connects
        if not on("copilot"):
            app.state.copilot.stop()
            await run_on(PROCESSES, app.state.overlay.stop)
        if not on("music"):
            app.state.music.silence()
        player = getattr(app.state, "player", None)  # set by the lifespan
        if player is not None:
            if on("quiz_public") and not player.running:
                await player.start()
            elif not on("quiz_public") and player.running:
                await player.stop()
        logger.info("ℹ️ features applied — off: %s", ", ".join(features.off_keys(app.state.config)) or "none")
        app.state.live.push_state()

    return apply
