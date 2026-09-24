"""Story 8: the magic map — people pop in with their names, a crowded region
gets an inset, a click tells who is where, an unplaced answer is fixed in one click."""

from __future__ import annotations

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session

ANSWERS = ["Madrid", "Barcelona", "Sevilla, España", "Valencia", "Bilbao", "Madrid, España", "Lisboa - Portugal",
           "CDMX", "Buenos Aires", "Milan, italy", "Grnada", "Bogotá"]


def test_people_land_on_the_map(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "map-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    expect(page.locator(".p-sub")).to_contain_text("1 of 17")
    page.keyboard.press("ArrowRight")  # item 2: the map
    expect(page.locator(".p-item .p-meta")).to_contain_text("Where are you joining from?")

    stage_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    stage = stage_ctx.new_page()
    stage.goto(f"{webapp.base_url}/stage")
    page.keyboard.press("Space")
    assert page.request.post(f"{webapp.base_url}/api/chat/simulate",
                             data={"kind": "list", "every_ms": 80, "answers": ANSWERS}).ok
    expect(page.locator("[data-capcounts]")).to_contain_text("12 answers", timeout=20000)

    # everyone but "Grnada" is on the map, Spain gets its inset
    expect(stage.locator(".mp-big")).to_contain_text("11")
    expect(stage.locator(".mp-inset")).to_be_visible()
    expect(stage.locator(".mp-inset-title")).to_contain_text("España")

    # the presenter offers the fix for "Grnada"
    fix = page.locator("[data-unplaced] [data-place]", has_text="Granada, ES")
    expect(fix).to_be_visible()
    fix.click()
    expect(page.locator("[data-unplaced]")).to_have_count(0)
    expect(stage.locator(".mp-big")).to_contain_text("12")

    # click a pin on the stage: who, city, country
    stage.locator(".mp-area > .mp-pins .mp-pin", has_text="Morgan").first.click()
    expect(stage.locator(".mp-pop")).to_contain_text("Mexico City")
    stage.mouse.click(5, 5)
    shot(page, shots / "story-08-map-1-presenter.png")
    shot(stage, shots / "story-08-map-2-stage.png")
    stage_ctx.close()
