"""The main page keeps its request budget on a fake clock.

A visible minute within the `.fleet.toml` budget, a hidden minute that reads nothing but the
usage log's one flush, and one catch-up read per path on the return. It lives in its own module
so the WebKit rerun (`scripts/tools/webkit_budget_check.py`) runs this test alone.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Browser

from tests.e2e.budget._request_budget import assert_request_budget, measure_request_budget, visible_budget

REPO_ROOT = Path(__file__).resolve().parents[3]
# The probe's own context: its fake clock and hidden-state script outlive a test, so no other test leases these arguments.
BUDGET_CONTEXT = {"viewport": {"width": 1366, "height": 768}}
# The one request a hidden page may make: the usage log sends its queue as a beacon on the switch
# to hidden so a visit's events are not lost, and arms nothing after that.
HIDING_FLUSH = "/api/usage/events"


def test_main_page_keeps_its_request_budget(browser: Browser, webapp) -> None:
    """The main page reads within its visible budget, nothing hidden but the usage flush, once per path on return."""
    context = browser.new_context(**BUDGET_CONTEXT)
    try:
        reading = measure_request_budget(context.new_page(), f"{webapp.base_url}/")
    finally:
        context.close()
    assert reading.hidden.pop(HIDING_FLUSH, 0) <= 1, f"more than the one flush on hiding: {reading.summary()}"
    assert_request_budget(reading, visible_budget(REPO_ROOT))
