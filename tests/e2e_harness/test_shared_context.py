"""The e2e suite leases browser contexts from one pool and resets them between tests.

A context that outlives its test is only safe if what a test leaves in it is
undone, and if no test leaves what the reset cannot undo. Pinned here against
recording stand-ins: the reset's contents and order, reuse keyed by context
arguments, ``close()`` handing a context back, two live leases of the same
arguments, a failed reset discarding the context, and a static check that no
story sets context-level state the reset cannot undo. No browser is started.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.e2e._shared_context import STATS, SharedContexts, context_key, reset_context

_E2E = Path(__file__).resolve().parents[1] / "e2e"
ORIGINS = ["http://app.test", "http://player.test"]


class _Page:
    def __init__(self, log: list[str], label: str) -> None:
        self._log, self._label = log, label

    def close(self) -> None:
        self._log.append(f"close {self._label}")

    def route(self, pattern: str, handler: Any) -> None:
        self._log.append(f"route {pattern}")

    def goto(self, url: str) -> None:
        self._log.append(f"goto {url}")

    def evaluate(self, script: str) -> None:
        self._log.append("clear local storage")


class _Context:
    def __init__(self, fail_on: str = "") -> None:
        self.log: list[str] = []
        self.really_closed = False
        self._fail_on = fail_on
        self._on_close: list[Callable[..., None]] = []
        self.pages = [_Page(self.log, "page-1"), _Page(self.log, "page-2")]

    def _step(self, name: str) -> None:
        self.log.append(name)
        if name == self._fail_on:
            raise RuntimeError(name)

    def unroute_all(self, behavior: str) -> None:
        self._step("unroute_all")

    def clear_cookies(self) -> None:
        self._step("clear_cookies")

    def clear_permissions(self) -> None:
        self._step("clear_permissions")

    def storage_state(self) -> dict[str, Any]:
        self._step("storage_state")
        return {"cookies": [], "origins": [{"origin": o, "localStorage": []} for o in ORIGINS]}

    def new_page(self) -> _Page:
        return _Page(self.log, "scratch")

    def on(self, event: str, handler: Callable[..., None]) -> None:
        self._on_close.append(handler)

    def close(self) -> None:
        self.really_closed = True
        for handler in self._on_close:
            handler(self)


class _Browser:
    def __init__(self) -> None:
        self.made: list[_Context] = []

    def new_context(self, **args: Any) -> _Context:
        context = _Context()
        self.made.append(context)
        return context


def _pool() -> tuple[_Browser, SharedContexts]:
    browser = _Browser()
    return browser, SharedContexts(browser)  # type: ignore[arg-type]


def test_reset_closes_pages_then_clears_what_a_context_keeps_without_a_socket_to_the_app() -> None:
    context = _Context()

    reset_context(context)  # type: ignore[arg-type]

    assert context.log == [
        "close page-1", "close page-2", "unroute_all", "clear_cookies", "clear_permissions", "storage_state",
        "route http://app.test/**", "goto http://app.test/", "clear local storage", "close scratch",
        "route http://player.test/**", "goto http://player.test/", "clear local storage", "close scratch",
    ]


def test_the_same_arguments_reuse_one_context_and_different_arguments_get_another() -> None:
    browser, shared = _pool()
    phone: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "is_mobile": True}
    desktop: dict[str, Any] = {"viewport": {"width": 1280, "height": 720}}

    first = shared.acquire(phone)
    shared.release(first, phone)
    assert shared.acquire(dict(reversed(list(phone.items())))) is first
    assert shared.acquire(desktop) is not first
    assert len(browser.made) == 2


def test_context_key_ignores_argument_order() -> None:
    assert context_key({"a": 1, "b": 2}) == context_key({"b": 2, "a": 1})
    assert context_key({"a": 1}) != context_key({"a": 2})


def test_closing_a_leased_context_resets_and_returns_it_instead_of_closing_it() -> None:
    browser, shared = _pool()
    args: dict[str, Any] = {"viewport": {"width": 1280, "height": 720}}
    context = shared.acquire(args)

    context.close()

    assert not browser.made[0].really_closed
    assert "clear_cookies" in browser.made[0].log
    assert shared.acquire(args) is context


def test_install_makes_a_storys_own_new_context_a_lease() -> None:
    browser, shared = _pool()
    shared.install()
    first = browser.new_context(viewport={"width": 1280, "height": 720})  # type: ignore[call-arg]
    first.close()

    assert browser.new_context(viewport={"width": 1280, "height": 720}) is first  # type: ignore[call-arg]
    assert len(browser.made) == 1


def test_two_live_leases_with_the_same_arguments_are_two_contexts_and_one_is_kept() -> None:
    browser, shared = _pool()
    args: dict[str, Any] = {"locale": "en-GB"}
    one, two = shared.acquire(args), shared.acquire(args)
    assert one is not two

    one.close()
    two.close()

    assert [c.really_closed for c in browser.made] == [False, True]
    assert shared.acquire(args) is one


def test_a_failed_reset_discards_the_context_so_the_next_test_gets_a_fresh_one() -> None:
    browser, shared = _pool()
    args: dict[str, Any] = {"locale": "en-GB"}
    damaged = shared.acquire(args)
    browser.made[0]._fail_on = "clear_cookies"
    discarded_before = STATS["discarded"]

    shared.release(damaged, args)

    assert browser.made[0].really_closed
    assert STATS["discarded"] == discarded_before + 1
    assert shared.acquire(args) is not damaged


def test_a_context_that_closed_for_real_is_never_leased_again() -> None:
    browser, shared = _pool()
    args: dict[str, Any] = {"locale": "en-GB"}
    context = shared.acquire(args)
    shared.release(context, args)

    browser.made[0].close()  # the browser went away under the pool

    assert shared.acquire(args) is not context


# Context-level state the reset does not undo: it would run in the next test that leases the context.
_CONTEXT_STATE = re.compile(
    r"\b(?:context|\w+_ctx|ctx|desk|stranger)\.(?:add_init_script|set_extra_http_headers|set_offline|"
    r"set_geolocation|route_from_har|expose_function|expose_binding|clock)\b|\.context\.(?:add_init_script|"
    r"set_extra_http_headers|set_offline|set_geolocation|route_from_har|expose_function|expose_binding|clock)\b"
)


def test_no_story_sets_context_level_state_the_reset_cannot_undo() -> None:
    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in sorted(_E2E.glob("test_*.py"))
        for match in _CONTEXT_STATE.finditer(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, f"build a context of its own for: {offenders}"
