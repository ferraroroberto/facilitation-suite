"""A flaky link on cue: the stories fail a phone's first send and see the page send it again."""

from __future__ import annotations

from typing import Optional

from playwright.sync_api import Page, Route


def fail_first(page: Page, path: str, status: Optional[int] = None) -> list[str]:
    """Fail the first request to ``path`` (``status`` as the answer, a dropped connection without one)
    and let the later ones through; returns the list of bodies sent, one per attempt."""
    sent: list[str] = []

    def handle(route: Route) -> None:
        sent.append(route.request.post_data or "")
        if len(sent) > 1:
            route.continue_()
        elif status is None:
            route.abort()
        else:
            route.fulfill(status=status, json={"error": {"code": "slow_down", "detail": {"retry_after_s": 0.5}}})

    page.route(f"**{path}", handle)
    return sent
