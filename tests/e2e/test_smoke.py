"""Smoke: the app shell loads in both themes, the nav switches panes, Settings opens."""

from __future__ import annotations

from playwright.sync_api import Page, expect


def test_shell_loads_and_navigates(page: Page, webapp) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(webapp.base_url + "/")
    expect(page.locator("#paneSessions .home-head")).to_have_count(1)
    expect(page.locator("#buildReadout")).to_contain_text("Build:")

    page.click("#tabPlan")
    expect(page.locator("#panePlan")).to_be_visible()
    expect(page.locator("#paneSessions")).to_be_hidden()

    page.locator("#panePlan [data-open-settings]").click()
    expect(page.locator("#paneSettings")).to_be_visible()

    before = page.evaluate("document.documentElement.dataset.theme")
    page.locator("#paneSettings [data-theme-toggle]").click()
    after = page.evaluate("document.documentElement.dataset.theme")
    assert before != after
    assert errors == []
