"""The e2e page-error recorder drops the benign WebKit ResizeObserver notice and keeps real errors."""

from __future__ import annotations

from tests.e2e._page_errors import is_benign, record_page_errors


class _FakePage:
    def __init__(self) -> None:
        self.handlers: list = []

    def on(self, event: str, handler) -> None:
        assert event == "pageerror"
        self.handlers.append(handler)


def test_resize_observer_notice_is_benign_and_real_errors_are_kept() -> None:
    page, errors = _FakePage(), []
    record_page_errors(page, errors)
    for message in (
        "ResizeObserver loop completed with undelivered notifications.",
        "ResizeObserver loop limit exceeded",
        "TypeError: x is undefined",
    ):
        page.handlers[0](message)
    assert errors == ["TypeError: x is undefined"]
    assert is_benign("ResizeObserver loop limit exceeded") and not is_benign("ReferenceError: nope")
