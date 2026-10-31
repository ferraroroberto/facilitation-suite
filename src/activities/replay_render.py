"""Draw replayed captures through the real stage: ``python -m src.activities.replay <folder> --render``.

Copies the minimum of the session (``session.yaml``, ``groups.yaml``, its
``theme.css``, ``slides/``) into a temp folder under the output, puts the
replayed ``<item>.json`` records in its ``live/captures/``, boots a disposable
server on a free loopback port with its own temp config, ledger and data dir
(never the live app, its config or its session folders), makes that copy live
and screenshots ``/stage?freeze=<item>`` with ``src.live.freeze.render``, so the
PNG comes from the same ``stage.js`` the session uses. The temp copy is removed
afterwards; the PNGs land beside the replayed records.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from src.config import CONFIG_SAMPLE_PATH, PROJECT_ROOT, AppConfig, load_config
from src.library import THEME_PREFIX, library_theme_css, themes_dir
from src.live.capture import CAPTURES_FILE
from src.live.freeze import render
from src.no_window import NO_WINDOW
from src.sessions.model import parse_session
from src.sessions.store import SESSION_FILE

logger = logging.getLogger("replay")

COPIED = (SESSION_FILE, "groups.yaml", "theme.css")
BOOT_TIMEOUT_S = 30
RMTREE_TRIES = 20  # × 0.25 s


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _call(base: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — our own disposable server on loopback
        return json.loads(r.read())


def _stage_copy(folder: Path, out: Path, item_ids: list[str], root: Path) -> Path:
    """The session's minimum plus the replayed records, as a session folder of its own."""
    copy = root / "session"
    (copy / "live" / "captures").mkdir(parents=True)
    for name in COPIED:
        if (folder / name).is_file():
            shutil.copy2(folder / name, copy / name)
    if (folder / "slides").is_dir():
        shutil.copytree(folder / "slides", copy / "slides")
    # A library theme lives beside the real session folders, which the disposable server does not see.
    theme = parse_session(yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))).theme
    css = library_theme_css(load_config(), theme)
    if css:
        themes = themes_dir(AppConfig(session_root=str(root / "sessions")))
        themes.mkdir(parents=True, exist_ok=True)
        (themes / f"{theme[len(THEME_PREFIX):]}.css").write_text(css, encoding="utf-8")
    windows = {}
    for item_id in item_ids:
        shutil.copy2(out / f"{item_id}.json", copy / "live" / "captures" / f"{item_id}.json")
        windows[item_id] = json.loads((out / f"{item_id}.json").read_text(encoding="utf-8"))["windows"]
    (copy / "live" / CAPTURES_FILE).write_text(json.dumps({"windows": windows, "hidden": [], "places": {}}), encoding="utf-8")
    return copy


def _boot(root: Path) -> tuple[subprocess.Popen, str]:
    """A disposable server under ``root``: OBS, the chat reader and the quiz player off."""
    cfg = json.loads(CONFIG_SAMPLE_PATH.read_text(encoding="utf-8"))
    port = _free_port()
    cfg.update(port=port, session_root=str(root / "sessions"))
    cfg["obs"]["enabled"] = cfg["reader"]["enabled"] = False
    cfg["quiz"]["public_port"] = 0
    (root / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    env = dict(os.environ, FS_CONFIG_PATH=str(root / "config.json"), FS_LEDGER_PATH=str(root / "sessions.local.yaml"),
               FS_DATA_DIR=str(root / "data"), FS_ENV_PATH=str(root / ".env"), PYTHONUTF8="1")
    log = open(root / "server.log", "wb")  # noqa: SIM115 — the child keeps its own handle
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.webapp.server:app", "--host", "127.0.0.1", "--port", str(port),
         "--loop", "app.webapp.event_loop:selector_loop_factory", "--log-level", "warning"],
        cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
    )
    log.close()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1):  # noqa: S310 — loopback
                return proc, base
        except OSError:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError(f"the render server did not start within {BOOT_TIMEOUT_S} s")


def render_all(folder: Path, out: Path, item_ids: list[str]) -> list[str]:
    """``<item>.png`` in ``out`` for every replayed item whose type draws on the stage; returns the ids that failed."""
    from src.activities.registry import editors

    def draws(item_id: str) -> bool:
        record = json.loads((out / f"{item_id}.json").read_text(encoding="utf-8"))
        return bool((editors().get(record["item"]["type"]) or {}).get("has_stage"))

    item_ids = [i for i in item_ids if draws(i)]
    if not item_ids:
        return []
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="render-", dir=out))
    failed: list[str] = []
    proc = None
    try:
        copy = _stage_copy(folder, out, item_ids, root)
        proc, base = _boot(root)
        sid = _call(base, "/api/sessions/add", {"path": str(copy)})["id"]
        _call(base, "/api/live/activate", {"session": sid})
        for item_id in item_ids:
            if not render(f"{base}/stage?freeze={item_id}&session={sid}", out / f"{item_id}.png"):
                failed.append(item_id)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("❌ render failed: %s (server log: %s)", exc, root / "server.log")
        return item_ids
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    # Windows releases the stopped server's log file a moment after the process ends.
    for _ in range(RMTREE_TRIES):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            break
        time.sleep(0.25)
    else:
        logger.warning("⚠️ the render's temp copy could not be removed: %s", root)
    logger.info("✅ %d of %d results drawn into %s", len(item_ids) - len(failed), len(item_ids), out)
    return failed
