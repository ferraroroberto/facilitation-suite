"""Story 5: go live — the stage and the presenter follow one state; keys, blackout, timer, clocks."""

from __future__ import annotations

import re

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session


def test_stage_and_presenter_stay_in_sync(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "live-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    expect(page.locator(".p-sub")).to_contain_text("1 of 17")

    stage_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    stage = stage_ctx.new_page()
    stage.on("pageerror", lambda e: errors.append(str(e)))
    stage.goto(f"{webapp.base_url}/stage")
    expect(stage.locator(".st-slide")).to_have_attribute("src", f"/api/sessions/{sid}/slides/slide-101.png")
    expect(page.locator(".p-chips")).to_contain_text("Stage · 1280×720")

    # → on the presenter moves the stage to the map activity (camera PiP, question on stage)
    page.keyboard.press("ArrowRight")
    expect(stage.locator(".st-question")).to_have_text("Where are you joining from?")
    expect(page.locator(".p-next .p-meta")).to_contain_text("Today's menu")
    expect(page.locator(".p-notes")).to_contain_text("Your city and country")

    # the item's own timer: T starts it on both screens
    page.keyboard.press("t")
    expect(stage.locator("[data-pill]")).to_be_visible()
    expect(page.locator("[data-tstate]")).to_have_text("running")
    page.locator("[data-tadd]").click()
    expect(page.locator("[data-tclock]")).to_have_text(re.compile(r"^(02:5\d|03:00)$"))  # 2 min + 1 min
    page.keyboard.press("t")
    expect(page.locator("[data-tstate]")).to_have_text("paused")

    # the clicker on the stage window drives the presenter too (PageDown = next)
    stage.keyboard.press("PageDown")
    expect(page.locator(".p-sub")).to_contain_text("3 of 17")

    # presenter-only clocks
    page.locator("[data-cstart]").click()
    expect(page.locator("[data-secname]")).to_have_text("Welcome")
    expect(page.locator("[data-drift]")).to_have_text("on time")
    expect(stage.locator("[data-secname]")).to_have_count(0)

    # blackout
    page.keyboard.press("b")
    expect(stage.locator("[data-blackout]")).to_be_visible()
    expect(page.locator("[data-flag]")).to_be_visible()
    page.keyboard.press("b")
    expect(stage.locator("[data-blackout]")).to_be_hidden()

    # the break opens with its on-enter timer running on the stage
    page.locator(".p-thumb", has_text="Coffee break").click()
    expect(stage.locator(".st-break-title")).to_have_text("Coffee break")
    expect(stage.locator("[data-clock]")).to_contain_text("09:")
    shot(page, shots / "story-05-live-1-presenter.png")
    shot(stage, shots / "story-05-live-2-stage.png")
    stage_ctx.close()
    assert errors == []
