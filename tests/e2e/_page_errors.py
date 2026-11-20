"""The e2e page-error recorder: one listener the stories share, so a message a browser engine
reports as a page error without a defect behind it is filtered in one place."""

from __future__ import annotations

import re

from playwright.sync_api import Page

# WebKit on macOS raises the ResizeObserver "undelivered notifications" notice as a page error;
# the spec calls it benign and Chromium never reports it.
_BENIGN = re.compile(r"ResizeObserver loop (limit exceeded|completed with undelivered notifications)")


def is_benign(message: str) -> bool:
    return _BENIGN.search(message) is not None


def record_page_errors(page: Page, errors: list[str]) -> None:
    """Append each real page error on ``page`` to ``errors``."""
    page.on("pageerror", lambda e: None if is_benign(str(e)) else errors.append(str(e)))
