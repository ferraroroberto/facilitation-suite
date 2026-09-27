"""OBS scene switching over obs-websocket v5 (built into OBS 28+; epic §11).

Every item has an OBS profile; each profile maps to one OBS scene (Settings).
When the live item changes, the scene follows. OBS being closed, restarting
or refusing a request is **never** fatal: a worker thread owns the
connection, reconnects with backoff, and reports one state for the
presenter's chip — each with its own message:

- ``off``          — switching is turned off in Settings
- ``connecting``   — trying now
- ``connected``    — the last request or heartbeat worked
- ``disconnected`` — OBS is not reachable (closed, or its WebSocket server is off)

A profile without a scene, or a scene OBS does not have, leaves OBS as it
is and says so in the detail; the deck keeps working either way.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, Optional

from src.config import AppConfig, profiles

logger = logging.getLogger(__name__)
# obsws-python logs a full traceback on every refused connection; with OBS
# closed that is one every few seconds. Our own one-line state says it.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)

HEARTBEAT_S = 5.0
BACKOFF_S = (1.0, 2.0, 5.0, 10.0)


class ObsService:
    def __init__(self, config: Callable[[], AppConfig], on_change: Callable[[], None] = lambda: None,
                 client_factory: Optional[Callable[..., Any]] = None) -> None:
        self.config = config
        self.on_change = on_change
        self._factory = client_factory
        self.client: Any = None
        self.state, self.detail = "connecting", ""
        self.version = ""
        self.scenes: list[str] = []
        self.profile: Optional[str] = None  # the profile OBS was last switched to
        self.scene: Optional[str] = None
        self.warning = ""  # a mapping problem (no scene / unknown scene)
        self._queue: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._failures = 0
        self.attempts = 0  # connection attempts so far (lets a caller wait for the next one)

    # --------------------------------------------------------------- control

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="obs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._queue.put(("stop", None))  # wakes a worker waiting on the queue
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._disconnect()

    def reconnect(self) -> None:
        """Drop the connection and try again now (Settings saved, or the chip clicked)."""
        self._queue.put(("reconnect", None))
        self._wake.set()

    def switch(self, profile: Optional[str]) -> None:
        """Ask for the scene of ``profile`` (queued; the worker does the talking)."""
        if profile:
            self._queue.put(("profile", profile))
            self._wake.set()

    # ----------------------------------------------------------------- state

    def _set(self, state: str, detail: str) -> None:
        if (state, detail) != (self.state, self.detail):
            if state != self.state:
                logger.info("ℹ️ obs: %s → %s (%s)", self.state, state, detail)
            self.state, self.detail = state, detail
            self._changed()

    def _changed(self) -> None:
        if self._stop.is_set():
            return  # shutting down: nobody to tell
        try:
            self.on_change()
        except RuntimeError:  # the server's event loop is already closed
            pass

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "detail": self.detail, "profile": self.profile, "scene": self.scene,
                "warning": self.warning, "version": self.version}

    def readiness(self) -> dict[str, Any]:
        cfg = self.config()
        if not cfg.obs.enabled:
            return {"enabled": False, "connected": False, "detail": "Scene switching is off in Settings"}
        mapped = sum(1 for p in profiles(cfg).values() if p["scene"])
        if self.state != "connected":
            return {"enabled": True, "connected": False, "detail": self.detail or "Not connected"}
        return {"enabled": True, "connected": True, "detail": f"OBS {self.version} · {mapped} of 3 profiles have a scene"}

    # ---------------------------------------------------------------- worker

    def _connect(self) -> bool:
        cfg = self.config().obs
        self._set("connecting", f"Connecting to OBS at {cfg.host}:{cfg.port}")
        self.attempts += 1
        try:
            if self._factory is not None:
                client = self._factory(host=cfg.host, port=cfg.port, password=cfg.password, timeout=3)
            else:
                import obsws_python as obs

                client = obs.ReqClient(host=cfg.host, port=cfg.port, password=cfg.password, timeout=3)
            self.version = str(getattr(client.get_version(), "obs_version", ""))
            self.scenes = [s["sceneName"] for s in client.get_scene_list().scenes]
        except Exception as exc:  # noqa: BLE001 — refused, timeout, auth: all mean "not connected"
            self._set("disconnected", f"OBS is not reachable at {cfg.host}:{cfg.port} — open OBS and turn on its WebSocket server ({type(exc).__name__})")
            return False
        self.client = client
        self._failures = 0
        self._set("connected", f"OBS {self.version} · {len(self.scenes)} scenes")
        return True

    def _disconnect(self) -> None:
        c, self.client = self.client, None
        if c is not None:
            try:
                c.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _apply(self, profile: str) -> None:
        mapping = profiles(self.config())
        scene = (mapping.get(profile) or {}).get("scene") or ""
        label = (mapping.get(profile) or {}).get("label") or profile
        if not scene:
            self.warning = f"No OBS scene set for {label} — OBS stays on its current scene"
            self.profile = profile
            self._changed()
            return
        if self.scenes and scene not in self.scenes:
            self.warning = f"OBS has no scene called {scene!r} ({label})"
            self._changed()
            return
        self.client.set_current_program_scene(scene)
        self.warning = ""
        self.profile, self.scene = profile, scene
        logger.info("ℹ️ obs: scene → %s (%s)", scene, label)
        self._changed()

    def _run(self) -> None:
        pending: Optional[str] = None  # the profile to apply once connected
        while not self._stop.is_set():
            if not self.config().obs.enabled:
                self._disconnect()
                self._set("off", "Scene switching is off in Settings")
                self._wake.wait(2.0)
                self._wake.clear()
                self._drain(lambda kind, arg: None)
                continue
            if self.client is None and not self._connect():
                self._failures += 1
                self._wake.wait(BACKOFF_S[min(self._failures - 1, len(BACKOFF_S) - 1)])
                self._wake.clear()
                pending = self._drain(lambda kind, arg: None, pending)
                continue
            if pending:
                profile, pending = pending, None
                self._safe(lambda p=profile: self._apply(p))
            try:
                kind, arg = self._queue.get(timeout=HEARTBEAT_S)
            except queue.Empty:
                client = self.client
                if client is not None:
                    self._safe(client.get_version)
                continue
            if kind == "stop":
                break
            if kind == "reconnect":
                self._disconnect()
            elif kind == "profile":
                if not self._safe(lambda a=arg: self._apply(a)):
                    pending = arg  # retried after the reconnect

    def _drain(self, handle: Callable[[str, Any], None], pending: Optional[str] = None) -> Optional[str]:
        """Empty the queue while not connected, remembering the latest profile asked for."""
        while True:
            try:
                kind, arg = self._queue.get_nowait()
            except queue.Empty:
                return pending
            if kind == "profile":
                pending = arg
            handle(kind, arg)

    def _safe(self, fn: Callable[[], Any]) -> bool:
        try:
            fn()
            return True
        except Exception as exc:  # noqa: BLE001 — any failure means the connection is gone
            logger.warning("⚠️ obs: request failed (%s) — reconnecting", exc)
            self._disconnect()
            self._set("disconnected", f"Lost OBS ({type(exc).__name__}) — reconnecting")
            return False
