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
    area = stage.locator(".mp-area").bounding_box()

    # the lowest and the rightmost pin (map or inset) open their popover inside the area, not clipped by its edge
    pins = stage.locator(".mp-area .mp-pin")
    extremes = prev = None
    for _ in range(20):  # once the view has settled
        extremes = stage.evaluate("""() => {
          const r = [...document.querySelectorAll('.mp-area .mp-pin')].map((el, i) => [i, el.getBoundingClientRect()]).filter(([, b]) => b.width);
          const lowest = r.reduce((a, b) => (b[1].bottom > a[1].bottom ? b : a));
          const rightmost = r.reduce((a, b) => (b[1].right > a[1].right ? b : a));
          return [lowest[0], rightmost[0], lowest[1].bottom, rightmost[1].right];
        }""")
        if extremes == prev:
            break
        prev = extremes
        stage.wait_for_timeout(250)
    for i in set(extremes[:2]):
        pins.nth(i).click()
        pop = stage.locator(".mp-pop").bounding_box()
        assert (area["x"] <= pop["x"] and pop["x"] + pop["width"] <= area["x"] + area["width"]
                and area["y"] <= pop["y"] and pop["y"] + pop["height"] <= area["y"] + area["height"]), (pop, area)

    stage.mouse.click(area["x"] + 8, area["y"] + 8)  # closes the popover — a click that only closes it does not go on
    expect(stage.locator(".mp-pop")).to_have_count(0)
    expect(page.locator(".p-sub")).to_contain_text("2 of 17")

    # every cluster shows its count, and no two labels cover each other
    counts = stage.locator(".mp-pin.many b").all_inner_texts()
    assert counts and all(c.isdigit() and int(c) > 1 for c in counts), counts
    overlaps = stage.evaluate("""() => {
      const r = [...document.querySelectorAll('.mp-pin:not([hidden]) .mp-label')].filter((l) => l.offsetParent && l.textContent).map((l) => l.getBoundingClientRect());
      let n = 0;
      r.forEach((a, i) => r.slice(i + 1).forEach((b) => { if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) n += 1; }));
      return n;
    }""")
    assert overlaps == 0
    shot(page, shots / "story-08-map-1-presenter.png")
    shot(stage, shots / "story-08-map-2-stage.png")
    stage_ctx.close()
