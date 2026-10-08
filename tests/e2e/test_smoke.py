"""Smoke: the app shell loads in both themes, the nav switches panes, Settings opens in its five
sections and the presenter's music chip deep-links to Settings → Music (#110),
and on a phone the vendored nav owns `.app`'s padding (safe area on top, the
floating tab bar's reserve at the bottom) on both nav pages, where the rendered geometry also
holds: no sideways scroll, tabs that reach 44px and never overlap (#224). The quiz player
page answers beside it on its own port."""

from __future__ import annotations

import yaml
from playwright.sync_api import Browser, Page, expect

from tests.e2e._geometry import assert_min_target, assert_no_horizontal_overflow, assert_no_overlap
from tests.fixtures.demo import build_demo_session

# A touch phone (coarse pointer, <=520px): the vendored nav-tabs.css phone rule applies.
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
# top = safe area (0 in emulation) · sides = --gap · bottom = 21 + 61 + 21 + --gap
PHONE_PADDING = ["0px", "12px", "115px", "12px"]
APP_PADDING = """() => {
  const s = getComputedStyle(document.querySelector('.app'));
  return [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft];
}"""


def test_shell_loads_and_navigates(page: Page, browser: Browser, webapp) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(webapp.base_url + "/")
    expect(page.locator("#paneSessions .home-head")).to_have_count(1)
    expect(page.locator("#buildReadout")).to_contain_text("Build:")
    # desktop keeps the app's own --gap padding on the sides and bottom
    assert page.evaluate(APP_PADDING)[1:] == ["12px", "12px", "12px"]

    page.click("#tabPlan")
    expect(page.locator("#panePlan")).to_be_visible()
    expect(page.locator("#paneSessions")).to_be_hidden()

    page.locator("#panePlan [data-open-settings]").click()
    expect(page.locator("#paneSettings")).to_be_visible()
    expect(page.locator("#paneSettings .settings-section-title")).to_have_text(
        ["Appearance", "Stage defaults", "Music", "Live tools", "About"])

    before = page.evaluate("document.documentElement.dataset.theme")
    with page.expect_response("**/api/settings/appearance"):  # #92: it sets the global appearance
        page.locator("#paneSettings [data-theme-toggle]").click()
    after = page.evaluate("document.documentElement.dataset.theme")
    assert before != after
    # put it back for the stories after this one
    assert page.request.put(webapp.base_url + "/api/settings/appearance", data={"appearance": "system"}).ok
    assert errors == []

    # #110: the presenter's music chip opens Settings → Music in the app's window (no Spotify login here)
    folder = webapp.root / "sessions" / "demo" / "smoke-music"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    raw = yaml.safe_load((folder / "session.yaml").read_text(encoding="utf-8"))
    raw["sections"][0]["items"][0]["music"] = {"source": "file", "path": "audio/synthetic.mp3", "start": "manual"}
    (folder / "session.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert page.request.post(webapp.base_url + "/api/live/activate", data={"session": sid}).ok
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    chip = page.locator('.p-chips a.chip[href="/#settings/music"]')
    expect(chip).to_contain_text("Music · idle")
    with page.context.expect_page() as opened:
        chip.click()
    app_tab = opened.value
    expect(app_tab.locator("[data-section=music]")).to_be_in_viewport()
    expect(app_tab.locator("[data-spotify-state]")).to_have_text("Not set up")
    expect(app_tab.locator("[data-connect]")).to_be_disabled()  # no client id in .env
    app_tab.close()
    assert page.request.post(webapp.base_url + "/api/live/deactivate").ok
    assert errors == []

    # #49/#52: the quiz player page answers beside the app on its own (free) port.
    page.goto(webapp.player_url + "/play?pin=123456")
    expect(page.locator("#joinForm")).to_be_visible()
    expect(page.locator("#pin")).to_have_value("123456")
    expect(page.locator("#conn")).to_have_attribute("data-state", "online")  # the ping answered
    assert errors == []

    # #46: app.css loads after nav-tabs.css, so an unscoped `.app { padding }`
    # there silently clobbers the nav's phone padding.
    phone = browser.new_context(**PHONE)
    try:
        p = phone.new_page()
        for path in ("/", "/remote"):
            p.goto(webapp.base_url + path)
            expect(p.locator(".app")).to_have_count(1)
            assert p.evaluate(APP_PADDING) == PHONE_PADDING, path
            # #224: the rendered leg -- effective hit rectangles and overflow, which no static scan proves
            tabs = p.locator(".tabs .tab")
            assert_no_horizontal_overflow(p)
            assert_min_target(tabs)
            assert_no_overlap(tabs)
    finally:
        phone.close()
