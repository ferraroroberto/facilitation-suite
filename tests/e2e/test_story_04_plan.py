"""Story 4: plan the session — edit an activity, give it its own timer, notes and a
title, break its question over two lines, duplicate it, reorder, skip, fold the
sections, add one, save."""

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

    # fold every section, open them again
    page.locator("[data-fold=collapse]").click()
    expect(page.locator(".item-row")).to_have_count(0)
    page.locator("[data-fold=expand]").click()
    expect(page.locator(".item-row")).to_have_count(18)

    # the closing word cloud: a title, a question on two lines, font size, its own 90 s timer, notes
    page.locator(".item-row", has_text="What do you take away today?").click()
    page.locator(".ed-row", has_text="Title").locator("input").fill("Take-home word")
    page.get_by_placeholder("What did you learn about this group?").fill(r"One word\nyou take home")
    page.locator(".ed-row", has_text="Question font").locator("input").fill("88")
    page.locator(".timer-box .toggle").click()
    page.locator(".dur-input").fill("1:30")
    page.get_by_label("Notes").fill("Read the top three aloud.")
    expect(page.locator(".dirty-bar")).to_be_visible()
    expect(page.locator(".preview-frame .st-question")).to_have_js_property("innerHTML", "One word<br>you take home")
    expect(page.locator(".item-row.selected .item-title")).to_have_text("Take-home word")

    # duplicate it: the copy follows it and is selected
    page.locator(".ed-tools [data-dup]").click()
    expect(page.locator(".sec-row", has_text="Closing")).to_contain_text("3 items")

    # skip the slide after it, and move the closing section's slide to the top
    page.locator(".item-row", has_text="Thank you!").click()
    page.locator(".ed-row", has_text="In this session").locator(".toggle").click()
    page.locator("[data-up]").click()
    page.locator("[data-up]").click()

    # a new section right after "Working agreement", from its add menu
    page.locator(".add-row").nth(3).click()
    page.locator(".row-menu-item", has_text="Section after this one").click()
    page.fill("#f-name", "Energiser")
    page.locator(".detail-save-btn").click()
    expect(page.locator(".sec-row").nth(4)).to_contain_text("Energiser")
    page.locator(".item-row", has_text="Take-home word").first.click()
    expect(page.locator(".preview-frame .wc-word").first).to_be_visible()  # sample answers drawn
    shot(page, shots / "story-04-plan-1-desktop.png")
    page.locator(".ed-save").click()
    expect(page.locator(".dirty-bar")).to_have_count(0)

    saved = yaml.safe_load((Path(folder) / "session.yaml").read_text(encoding="utf-8"))
    assert [s["name"] for s in saved["sections"]] == ["Welcome", "Personal readme", "Break", "Working agreement", "Energiser", "Closing"]
    closing = saved["sections"][-1]["items"]
    assert [i.get("slide_id") or i["id"] for i in closing][:2] == [110, "act-takeaway"]
    assert closing[0]["include"] is False
    act, copy = closing[1], closing[2]
    assert act["title"] == "Take-home word"
    assert act["question"] == r"One word\nyou take home"
    assert act["font"]["size_px"] == 88
    assert act["notes"] == "Read the top three aloud."
    assert act["timer"] == {"enabled": True, "seconds": 90, "start": "with_capture", "show_on": "stage", "end": "stop_capture"}
    assert copy["id"] != act["id"] and {k: v for k, v in copy.items() if k != "id"} == {k: v for k, v in act.items() if k != "id"}
    # untouched items keep no timer: timers are decided item by item
    assert "timer" not in saved["sections"][0]["items"][0]
