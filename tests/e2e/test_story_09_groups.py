"""Story 9: breakout groups — mark who is here, shuffle three rounds, read the
mixing note, and add the "who are you with?" reveal to the plan — as an unsaved
edit of an already open plan, drawn with the real rooms."""

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
    page.click("#tabPlan")  # the plan is already open when the reveal is added
    expect(page.locator(".sec-row", has_text="Personal readme")).to_contain_text("5 items")
    page.locator('[data-item="slide-105"]').click()
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
    expect(page.locator("#toast")).to_contain_text("added to Personal readme")
    page.click("#tabPlan")
    # right after the selected slide, unsaved, drawn with the rooms just shuffled
    expect(page.locator(".sec-row", has_text="Personal readme")).to_contain_text("6 items")
    expect(page.locator(".dirty-bar")).to_contain_text("Unsaved changes")
    added = page.locator(".item-row.selected")
    expect(added).to_contain_text("Groups of 4 (B)")
    expect(page.locator(".preview-frame .gr-room")).to_have_count(9)  # 37 people in rooms of four (one of five)
    expect(page.locator(".preview-frame .st-sub")).to_have_text("Groups of 4 · B · 9 rooms")

    # the round picked in the plan changes the rooms and the line under the title
    page.locator(".opt-line", has_text="Round").locator("select").select_option("pairs")
    expect(page.locator(".preview-frame .st-sub")).to_have_text("Pairs · 18 rooms")
    expect(added).to_contain_text("Pairs")

    # a new shuffle redraws the preview with the new rooms
    first = page.locator(".preview-frame .gr-room").first.inner_text()
    page.click("#tabGroups")
    page.locator("[data-shuffle]").click()
    page.click("#tabPlan")
    expect(page.locator(".preview-frame .gr-room").first).not_to_have_text(first)
