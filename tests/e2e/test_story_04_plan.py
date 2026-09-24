"""Story 4: plan the session — edit an activity, give it its own timer, reorder, skip, save."""

from __future__ import annotations

from pathlib import Path

import yaml
from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session


def test_edit_the_plan_and_save_it(page: Page, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "plan-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    page.goto(webapp.base_url + "/")
    page.click("#tabPlan")
    expect(page.locator(".sec-row")).to_have_count(5)

    # the closing word cloud: new question, font size, its own 90 s timer
    page.locator(".item-row", has_text="What do you take away today?").click()
    page.locator(".ed-row", has_text="Question").locator("input").first.fill("One word you take home")
    page.locator(".ed-row", has_text="Question font").locator("input").fill("88")
    page.locator(".timer-box .toggle").click()
    page.locator(".dur-input").fill("1:30")
    expect(page.locator(".dirty-bar")).to_be_visible()

    # skip the slide after it, and move the closing section's slide above the activity
    page.locator(".item-row", has_text="Thank you!").click()
    page.locator(".ed-row", has_text="In this session").locator(".toggle").click()
    page.locator("[data-up]").click()
    shot(page, shots / "story-04-plan-1-desktop.png")
    page.locator(".ed-save").click()
    expect(page.locator(".dirty-bar")).to_have_count(0)

    saved = yaml.safe_load((Path(folder) / "session.yaml").read_text(encoding="utf-8"))
    closing = saved["sections"][-1]["items"]
    assert [i.get("slide_id") or i["id"] for i in closing] == [110, "act-takeaway"]
    assert closing[0]["include"] is False
    act = closing[1]
    assert act["question"] == "One word you take home"
    assert act["font"]["size_px"] == 88
    assert act["timer"] == {"enabled": True, "seconds": 90, "start": "with_capture", "show_on": "stage", "end": "stop_capture"}
    # untouched items keep no timer: timers are decided item by item
    assert "timer" not in saved["sections"][0]["items"][0]
