"""Smoke: the app shell loads in both themes, the nav switches panes, Settings opens,
and on a phone the vendored nav owns `.app`'s padding (safe area on top, the
floating tab bar's reserve at the bottom) on both nav pages. The quiz player
page answers beside it on its own port."""

from __future__ import annotations

from playwright.sync_api import Browser, Page, expect

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

    before = page.evaluate("document.documentElement.dataset.theme")
    with page.expect_response("**/api/settings/appearance"):  # #92: it sets the global appearance
        page.locator("#paneSettings [data-theme-toggle]").click()
    after = page.evaluate("document.documentElement.dataset.theme")
    assert before != after
    # put it back for the stories after this one
    assert page.request.put(webapp.base_url + "/api/settings/appearance", data={"appearance": "system"}).ok
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
    finally:
        phone.close()
