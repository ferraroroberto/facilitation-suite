"""Browser contexts that outlive the test that asked for them, reset between tests.

A fresh context per test starts with no sockets and an empty HTTP cache, so every
test pays its own connections to the app, and every one it closes parks a
loopback port in TIME_WAIT (see ``_port_pool``). Here a context is kept by its
arguments: ``page``'s context and every ``browser.new_context(**args)`` a story
opens (the stage window, the phones) are leased from one pool, and
``context.close()`` on a leased context resets it and hands it back instead of
closing it.

The reset covers the context state this suite touches: the pages (session
storage dies with them), the context's routes, cookies and permission grants,
and local storage on every origin the context holds any for (the app and the
quiz player listener are two origins). A test that sets context-level state this
reset does not undo (init scripts, extra headers, offline, geolocation, the page
clock) must not share a context: ``tests/e2e_harness`` fails the file that does.

Two live leases with the same arguments get two contexts; only one is kept when
both come back. A reset that raises, or a context that closes for real, leaves
the pool, so a damaged one never reaches the next test.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Route

logger = logging.getLogger(__name__)

#: How the shared contexts were used this session, for the run summary.
STATS = {"created": 0, "reused": 0, "discarded": 0}


def context_key(args: dict[str, Any]) -> str:
    """A stable identity for a set of context arguments."""
    return json.dumps(args, sort_keys=True, default=repr)


def _blank(route: Route) -> None:
    route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>reset</title>")


def reset_context(context: BrowserContext) -> None:
    """Return *context* to what a freshly created one holds.

    Each origin's storage is cleared from a scratch page whose requests a route
    answers, so the reset opens no socket to the server.
    """
    for page in list(context.pages):
        page.close()
    context.unroute_all(behavior="ignoreErrors")
    context.clear_cookies()
    context.clear_permissions()
    for entry in context.storage_state().get("origins", []):
        origin = entry["origin"]
        scratch = context.new_page()
        try:
            scratch.route(f"{origin}/**", _blank)
            scratch.goto(f"{origin}/")
            scratch.evaluate("() => localStorage.clear()")
        finally:
            scratch.close()


class SharedContexts:
    """The session's idle contexts on one browser, keyed by their context arguments."""

    def __init__(self, browser: Browser) -> None:
        self._browser = browser
        self._make: Callable[..., BrowserContext] = browser.new_context
        self._idle: dict[str, BrowserContext] = {}
        self._close: dict[BrowserContext, Callable[..., None]] = {}

    def install(self) -> None:
        """Make ``browser.new_context(**args)`` a lease from this pool."""
        self._browser.new_context = self.acquire  # type: ignore[method-assign]

    def acquire(self, args: dict[str, Any] | None = None, /, **kwargs: Any) -> BrowserContext:
        args = {**(args or {}), **kwargs}
        key = context_key(args)
        context = self._idle.pop(key, None)
        if context is not None:
            STATS["reused"] += 1
            return context
        context = self._make(**args)
        STATS["created"] += 1
        real_close = context.close
        self._close[context] = real_close
        context.on("close", lambda *_: self._forget(context))
        context.close = lambda *_a, **_k: self.release(context, args)  # type: ignore[method-assign]
        return context

    def release(self, context: BrowserContext, args: dict[str, Any]) -> None:
        """Reset *context* for the next lease, or drop it when the reset fails."""
        if context not in self._close:
            return
        try:
            reset_context(context)
        except Exception:
            logger.warning("⚠️ shared browser context reset failed; discarding it", exc_info=True)
            STATS["discarded"] += 1
            self._discard(context)
            return
        key = context_key(args)
        if key in self._idle:
            self._discard(context)  # a second lease with the same arguments: keep one
        else:
            self._idle[key] = context

    def _discard(self, context: BrowserContext) -> None:
        real_close = self._close.pop(context, None)
        for key in [k for k, c in self._idle.items() if c is context]:
            del self._idle[key]
        if real_close is not None:
            try:
                real_close()
            except Exception:
                logger.warning("⚠️ discarded context did not close cleanly", exc_info=True)

    def _forget(self, context: BrowserContext) -> None:
        """The context closed for real (browser gone, crash): it can never be leased again."""
        self._close.pop(context, None)
        for key in [k for k, c in self._idle.items() if c is context]:
            del self._idle[key]


def write_summary(terminalreporter: Any) -> None:
    total = STATS["created"] + STATS["reused"]
    if total:
        terminalreporter.write_line(
            f"shared contexts: {STATS['created']} created for {total} leases "
            f"({STATS['reused']} reused, {STATS['discarded']} discarded after a failed reset)"
        )
