"""Story 9: breakout groups — mark who is here, shuffle three rounds, read the
mixing note, and add the "who are you with?" reveal to the plan."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session


def test_shuffle_breakout_rounds(page: Page, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "groups-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    page.goto(webapp.base_url + "/")
    page.click("#tabGroups")
    expect(page.locator("#paneGroups .home-head .status")).to_have_text("38 of 40 present")

    # one more person is away today
    page.locator(".person-row", has_text="Alex R.").locator("[role=switch]").click()
    expect(page.locator("#paneGroups .home-head .status")).to_have_text("37 of 40 present")

    page.locator("#paneGroups .empty-state button", has_text="Shuffle").click()
    expect(page.locator(".range-tab", has_text="Pairs")).to_contain_text("18 rooms")  # 37 → 17 pairs + a trio
    expect(page.locator(".room-card")).to_have_count(18)
    expect(page.locator(".room-card", has_text="Alex R.")).to_have_count(0)

    page.locator(".range-tab", has_text="Groups of 4 · B").click()
    expect(page.locator(".banner.ok")).to_contain_text("Round B:")
    expect(page.locator("[data-csv]")).to_be_disabled()  # two people have no email
    expect(page.locator("#paneGroups")).to_contain_text("missing for")
    shot(page, shots / "story-09-groups-1-desktop.png")

    page.locator("[data-reveal]").click()
    expect(page.locator("#toast")).to_contain_text("Who are you with?")
    page.click("#tabPlan")
    expect(page.locator(".sec-row", has_text="Welcome")).to_contain_text("6 items")
