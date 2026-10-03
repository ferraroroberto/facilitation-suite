"""Story 5: go live — the stage and the presenter follow one state; keys, clicks, blackout, timer,
clocks, and starting the session over."""

from __future__ import annotations

import re

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session

# #190: draw items with the stage's own renderer into a 1920×1080 host (scale 1, so every number is a
# canvas pixel), once per case, and report where the title text sits against the camera zone's middle.
CENTRE_ON_CAMERA = """async (cases) => {
  const { createStage } = await import('/static/js/stage-render.js');
  await document.fonts.ready;
  const out = [];
  for (const c of cases) {
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:0;top:0;width:1920px;height:1080px;z-index:99';
    document.body.appendChild(host);
    const stage = createStage(host);
    const ctx = { plan: { session: { id: 'x' }, run: { language: 'en' } }, state: {}, now: 0, result: null };
    stage.render(Object.assign({ id: c.name, profile: 'camera_pip', zone: c.zone, font: {} }, c.item), ctx);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const top = host.getBoundingClientRect().top;
    const text = host.querySelector(c.kind === 'slide' ? '.st-tbox.title span' : '.st-question');
    const r = text.getBoundingClientRect();
    const zone = c.zone;
    out.push({ name: c.name, mid: (r.top + r.bottom) / 2 - top, zoneMid: zone ? ((zone[1] + zone[3]) / 2) * 1080 : null,
               lineHeight: parseFloat(getComputedStyle(text).lineHeight) / parseFloat(getComputedStyle(text).fontSize) });
    host.remove();
  }
  return out;
}"""
PIP = [0.72, 0.04, 0.98, 0.3]
CAMERA_CASES = [
    {"name": f"q{n}", "kind": "activity", "zone": PIP,
     "item": {"kind": "activity", "title": "\\n".join(["Where is the group strong?"] * n)}}
    for n in (1, 2, 3)
] + [
    {"name": "slide2", "kind": "slide", "zone": PIP,
     "item": {"kind": "slide", "slide_bg": "bg.png", "title": "Slide",
              "text_boxes": [{"x": 110, "y": 90, "w": 1100, "h": 230, "text": "One line\nTwo lines", "title": True,
                              "size": 72, "color": "#1f1f1f", "align": "left", "anchor": "top", "pad": [0, 0, 0, 0]}]}},
    # the other layouts keep the text where it was (top-aligned: well above the zone's middle)
    {"name": "screen_only", "kind": "activity", "zone": None, "item": {"kind": "activity", "title": "One line", "profile": "screen_only"}},
    {"name": "strip", "kind": "activity", "zone": [0.75, 0, 1, 1], "item": {"kind": "activity", "title": "One line", "profile": "camera_strip"}},
]


def _reflowed(page: Page) -> None:
    """A viewport resize has been laid out and its resize handlers have run (two frames)."""
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())))")


def test_stage_and_presenter_stay_in_sync(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "live-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    expect(page.locator(".p-sub")).to_contain_text("1 of 17")
    expect(page.locator(".p-music")).to_be_hidden()  # a session without music: no Music card, no chip

    # #98: the header wraps at phone width instead of pushing the page sideways
    page.set_viewport_size({"width": 390, "height": 844})
    _reflowed(page)
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    client_width = page.evaluate("document.documentElement.clientWidth")
    assert scroll_width <= client_width, f"presenter scrolls sideways at 390px ({scroll_width} > {client_width})"

    # #126: at tablet widths the title keeps a readable share of the row instead of
    # being squeezed to nothing by the chips and icon buttons
    for width in (600, 768, 1024):
        page.set_viewport_size({"width": width, "height": 900})
        _reflowed(page)
        title_width = page.locator(".p-title").bounding_box()["width"]
        assert title_width > 120, f"presenter title squeezed to {title_width}px at {width}px wide"
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width, f"presenter scrolls sideways at {width}px ({scroll_width} > {client_width})"
    page.set_viewport_size({"width": 1440, "height": 900})

    stage_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    stage = stage_ctx.new_page()
    stage.on("pageerror", lambda e: errors.append(str(e)))
    stage.goto(f"{webapp.base_url}/stage")
    # the opening divider: its text-free picture, and its title drawn in the stage font
    expect(stage.locator(".st-slide")).to_have_attribute("src", f"/api/sessions/{sid}/slides/slide-101-bg.png")
    expect(stage.locator(".st-tbox.title")).to_have_text("Welcome to the workshop")
    assert "Patrick Hand" in stage.locator(".st-tbox.title").evaluate("e => getComputedStyle(e).fontFamily")
    # the slide's other text is in the text font (the chat hint's plain sans), as typed
    tagline = stage.locator(".st-tbox:not(.title)")
    expect(tagline).to_have_text("Two hours, one team")
    assert "Patrick Hand" not in tagline.evaluate("e => getComputedStyle(e).fontFamily")
    expect(tagline).to_have_css("text-transform", "none")
    expect(page.locator(".p-chips")).to_contain_text("Stage · 1280×720")

    # → on the presenter moves the stage to the map activity (camera PiP, question on stage)
    page.keyboard.press("ArrowRight")
    expect(stage.locator(".st-question")).to_have_text("Where are you joining from?")
    expect(page.locator(".p-next .p-meta")).to_contain_text("Today's menu")
    expect(page.locator(".p-notes")).to_contain_text("Your city and country")

    # #190: under a corner camera the title text is centred on the camera's middle (1, 2 and 3 lines,
    # an imported slide's title box too) with tight line spacing; the other layouts do not move it
    got = {c["name"]: c for c in stage.evaluate(CENTRE_ON_CAMERA, CAMERA_CASES)}
    for name in ("q1", "q2", "q3", "slide2"):
        assert abs(got[name]["mid"] - got[name]["zoneMid"]) <= 4, got[name]
        assert got[name]["lineHeight"] <= 1.05, got[name]
    assert got["screen_only"]["mid"] < 140 and got["strip"]["mid"] < 140, got

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

    # a click on "on stage now" or on the stage window goes on; End and Home jump to the ends
    page.locator(".p-now .p-stage").click()
    expect(page.locator(".p-sub")).to_contain_text("11 of 17")
    stage.mouse.click(640, 360)
    expect(page.locator(".p-sub")).to_contain_text("12 of 17")
    page.keyboard.press("End")
    expect(page.locator(".p-sub")).to_contain_text("17 of 17")
    stage.keyboard.press("Home")
    expect(page.locator(".p-sub")).to_contain_text("1 of 17")

    # start over after the rehearsal: first item, no clock; the run so far is kept aside
    page.keyboard.press("End")
    page.locator("[data-reset]").click()
    page.locator(".dialog-confirm").click()
    expect(page.locator(".p-sub")).to_contain_text("1 of 17")
    expect(page.locator("[data-cstart]")).to_be_visible()
    assert [d.name.startswith("live-") for d in folder.iterdir()].count(True) == 1
    stage_ctx.close()
    assert errors == []
