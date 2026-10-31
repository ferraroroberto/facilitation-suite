"""The local listener: the main app again, in plain HTTP on ``127.0.0.1:<local_port>``.

The HTTPS port serves one certificate, which names only the tailnet host, so this
PC reaching ``https://127.0.0.1`` would meet a certificate warning on the presenter
and the stage. Browsers treat ``http://127.0.0.1`` as a secure context, so this
listener needs no certificate and no tailnet hop. Other devices keep the HTTPS URL.

Same lifecycle as the quiz player listener: both subclass ``EmbeddedListener``
(``app/player/listener.py``), which owns the bind, start and stop, and this one
supplies its own server config:

- **Loopback only, always.** Whatever ``config.host`` says, it binds ``127.0.0.1``;
  the app's auth gate already treats a loopback caller as this PC.
- **Same app, same loop.** A second ``uvicorn.Server`` serves the very same ASGI app
  (lifespan off: the main server runs it once) as a task on the main event loop.
- **Optional.** A busy port logs one ❌ line and the main app keeps serving; the tray
  then falls back to the HTTPS URL.
- ``local_port = 0`` turns it off (the unit tests do).
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from app.player.listener import EmbeddedListener

logger = logging.getLogger(__name__)


class LocalListener(EmbeddedListener):
    """Owns the local uvicorn server for the lifetime of the main app."""

    label = "local listener"
    task_name = "local-listener"
    off_setting = "local_port"
    busy_consequence = "this PC falls back to the HTTPS URL; the main app keeps serving."
    note = "this PC only"
    log = logger

    def __init__(self, port: int, app: FastAPI) -> None:
        super().__init__(port)
        self.app = app

    def _config(self) -> uvicorn.Config:
        return uvicorn.Config(
            self.app,
            lifespan="off",
            log_config=None,  # the process's logging is already configured; don't reset it
            access_log=True,  # False would empty the shared uvicorn.access logger for the main app too
            proxy_headers=False,  # nothing proxies in front of it: a forwarded header must not rewrite the client
            ws_ping_interval=20.0,
            ws_ping_timeout=20.0,
        )
