"""Playwright e2e fixtures — a disposable webapp per session, never the live :8449.

``webapp`` boots uvicorn on a free loopback port with ``FS_CONFIG_PATH`` → a
temp copy of the sample config (OBS off, the chat reader never started by going live),
``FS_LEDGER_PATH`` → a temp ledger, ``FS_DATA_DIR`` → a temp data dir and
``FS_ENV_PATH`` → an empty ``.env`` (no Spotify login), so a
run never reads or writes the real config, ledger or session folders. The copilot
talks to a fake hub in this process (``tests/fixtures/fake_hub.py``), never the real one.
The disposable instance always binds free ports (the app and the quiz player
listener), so a running tray on :8449/:8451 never collides with it.
The suite never targets the live app: its stories write (Settings, sessions, simulated
chat), so ``FS_E2E_LIVE`` is a refusal, not a mode.

Screenshots from a story go to ``docs/screenshots`` through ``shot()`` so the
repo carries the proof; fixtures are synthetic only. They are written only
when ``FS_E2E_SHOTS=1`` (a deliberate re-baseline), so a gate run never
rewrites the committed gallery.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext

from src.no_window import NO_WINDOW
from tests.conftest import SAMPLE_CONFIG, write_test_config
from tests.e2e._port_pool import pytest_runtest_makereport  # noqa: F401 — relabels a drained port pool
from tests.e2e._port_pool import write_summary as port_pool_summary
from tests.e2e._shared_context import SharedContexts
from tests.e2e._shared_context import write_summary as shared_context_summary
from tests.e2e._surface import pytest_collection_modifyitems  # noqa: F401
from tests.fixtures.fake_hub import FakeHub

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOTS_DIR = REPO_ROOT / "docs" / "screenshots"
LIVE_ENV = "FS_E2E_LIVE"  # setting it is refused: the stories are not read-only
LOOP_FACTORY = "app.webapp.event_loop:selector_loop_factory"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Instance:
    def __init__(self, base_url: str, root: Path, player_url: str = "") -> None:
        self.base_url = base_url
        self.root = root
        self.player_url = player_url  # the quiz player listener (app/webapp/player/)


def stop_instance(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    log = getattr(proc, "fs_log", None)
    if log is not None:
        log.close()


def boot_instance(root: Path, *, port: int = 0, player_port: int = 0,
                  **config: object) -> tuple[subprocess.Popen, Instance]:
    """Start a disposable server under ``root``; returns (process, instance).

    Free ports unless ``port`` / ``player_port`` are given (``scripts/quiz_bots.py``
    restarts an instance on the same ports, as a crash-and-restart would).
    """
    root.mkdir(parents=True, exist_ok=True)
    port = port or _free_port()
    player_port = player_port or _free_port()
    while player_port == port:
        player_port = _free_port()
    config.setdefault("quiz", {"public_port": player_port, "public_url": ""})
    write_test_config(root / "config.json", port=port, session_root=str(root / "sessions"), **config)
    env = os.environ.copy()
    env.update(
        FS_CONFIG_PATH=str(root / "config.json"),
        FS_LEDGER_PATH=str(root / "sessions.local.yaml"),
        FS_DATA_DIR=str(root / "data"),
        FS_USAGE_DB=str(root / "data" / "usage.sqlite3"),  # never the real usage log
        FS_ENV_PATH=str(root / ".env"),  # no real Spotify login in a disposable instance
        PYTHONUTF8="1",
    )
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_DEVICE_NAME"):
        env.pop(key, None)
    log = open(root / "server.log", "wb")  # noqa: SIM115 — closed with the process
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.webapp.serve", "--host", "127.0.0.1",
         "--port", str(port), "--loop", LOOP_FACTORY, "--log-level", "warning"],
        cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
    )
    proc.fs_log = log  # type: ignore[attr-defined] — closed by stop_instance()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    probe = http.client.HTTPConnection("127.0.0.1", port, timeout=1)  # one kept-alive socket, not one per poll
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"e2e webapp exited early; see {root / 'server.log'}")
            try:
                probe.request("GET", "/healthz")
                response = probe.getresponse()
                response.read()
                if response.status == 200:
                    return proc, Instance(base, root, f"http://127.0.0.1:{player_port}")
            except (OSError, http.client.HTTPException):
                probe.close()  # the next request reconnects
            time.sleep(0.25)
    finally:
        probe.close()
    stop_instance(proc)
    raise RuntimeError(f"e2e webapp did not answer /healthz within 30 s; see {root / 'server.log'}")


def fake_hub_copilot(hub: FakeHub) -> dict[str, object]:
    """The sample's copilot block aimed at a fake hub: no audio device, no overlay window, no real hub."""
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(hub_url=hub.url, capture="off")
    copilot["overlay"]["enabled"] = False
    return copilot


@pytest.fixture(autouse=True)
def _chromium_only(request: pytest.FixtureRequest, browser_name: str) -> None:
    """``needs_chromium`` stories wait on a Chromium-rendered file (the frozen capture PNG), so another engine skips them."""
    if request.node.get_closest_marker("needs_chromium") and browser_name != "chromium":
        pytest.skip(f"renders in Chromium; this run drives {browser_name}")


def _git_status() -> list[str]:
    """``git status`` of this checkout, read without taking the index lock (a read never strands one)."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    done = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO_ROOT, capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=60, check=True, creationflags=NO_WINDOW)
    return done.stdout.splitlines()


@pytest.fixture(scope="session", autouse=True)
def _leaves_git_status_clean() -> Iterator[None]:
    """A whole run (sessions, the live cockpit, simulated chat, several app instances started and stopped) adds
    nothing to ``git status``: every file the app writes is gitignored, so a plain ``git pull`` updates the PC.
    Compared with the status at the start, so a checkout with work in progress still runs it. A deliberate
    screenshot re-baseline (``FS_E2E_SHOTS=1``) rewrites tracked files and is left out."""
    if os.environ.get("FS_E2E_SHOTS") == "1":
        yield
        return
    before = set(_git_status())
    yield
    added = sorted(set(_git_status()) - before)
    assert not added, f"the e2e run left files git sees (gitignore them or write them outside the checkout): {added}"


@pytest.fixture(scope="session")
def webapp() -> Iterator[Instance]:
    if os.environ.get(LIVE_ENV):
        pytest.exit(f"{LIVE_ENV} is set: the e2e stories write, so they only run on a disposable instance", returncode=2)
    hub = FakeHub()
    with tempfile.TemporaryDirectory(prefix="fs-e2e-", ignore_cleanup_errors=True) as tmp:
        proc, inst = boot_instance(Path(tmp), copilot=fake_hub_copilot(hub))
        try:
            yield inst
        finally:
            stop_instance(proc)
            hub.close()


@pytest.fixture(scope="session")
def _shared_contexts(browser: Browser) -> SharedContexts:
    # Rebuilt per projection with `browser`; the contexts close with the browser. From here on a
    # story's own `browser.new_context(**args)` is a lease from the same pool as `page`'s context.
    shared = SharedContexts(browser)
    shared.install()
    return shared


@pytest.fixture
def context(
    request: pytest.FixtureRequest,
    _shared_contexts: SharedContexts,
    browser_context_args: dict,
    _artifacts_recorder: Any,
) -> Iterator[BrowserContext]:
    """The projection's shared context for this test's context arguments (``_shared_context``).

    Replaces pytest-playwright's per-test context, so the connections a test opens to the app
    are the ones the next test reuses. The recorder calls keep ``--tracing`` and ``--screenshot``
    working per test on a context that outlives it.
    """
    marker = next(request.node.iter_markers("browser_context_args"), None)
    args = {**browser_context_args, **(marker.kwargs if marker else {})}
    ctx = _shared_contexts.acquire(args)
    _artifacts_recorder.on_did_create_browser_context(ctx)
    yield ctx
    _artifacts_recorder.on_will_close_browser_context(ctx)
    _shared_contexts.release(ctx, args)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    shared_context_summary(terminalreporter)
    port_pool_summary(terminalreporter)


@pytest.fixture
def shots() -> Path:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SHOTS_DIR


def set_stage_click(page, instance: Instance, advance: bool) -> None:
    """Settings → Live tools → *A click on the stage goes to the next item* (#191), through its API,
    and a beat for the open stage window to hear it. The instance is shared by the whole run, so a
    test that turns it on turns it off again."""
    response = page.request.put(f"{instance.base_url}/api/settings", data={"stage_click": {"advance": advance}})
    assert response.ok, response.text()
    page.wait_for_timeout(300)


def shot(page, path: Path, settle: float = 0.0) -> None:
    """Capture for the gallery (only under ``FS_E2E_SHOTS=1``): no caret, no animations.
    ``settle`` is seconds to let a slow-drawing view finish first -- paid only when capturing."""
    if os.environ.get("FS_E2E_SHOTS") != "1":
        return
    if settle:
        page.wait_for_timeout(int(settle * 1000))
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(path), animations="disabled", caret="hide")


_SMALL_TARGETS_JS = """(floor) => {
  const out = [];
  for (const el of document.querySelectorAll(
      'button, a[href], input, select, textarea, summary, [role=button], [role=tab], [role=switch], [role=menuitem], [role=checkbox]')) {
    if (el.disabled) continue;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;
    // an invisible ::before expansion (the shared .hit-target, the vendored segmented) grows the effective box
    const b = getComputedStyle(el, '::before');
    const grow = (side) => (b.content !== 'none' && b.position === 'absolute') ? Math.max(0, -parseFloat(b[side]) || 0) : 0;
    const w = r.width + grow('left') + grow('right'), h = r.height + grow('top') + grow('bottom');
    if (w < floor - 1 || h < floor - 1)
      out.push(`${el.tagName.toLowerCase()}.${el.className} ${Math.round(w)}x${Math.round(h)}`);
  }
  return out;
}"""


def small_targets(page, floor: int = 44) -> list[str]:
    """Visible controls whose effective box (the visual box, plus the shared ``.hit-target`` expansion)
    is under ``floor`` px either way -- design.md hit-target.min (#167). Empty when the view conforms."""
    return page.evaluate(_SMALL_TARGETS_JS, floor)
