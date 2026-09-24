"""Story 2: create a session folder from the app, see it in the ledger with its readiness list."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot


def test_create_a_session_and_see_it_ready_list(page: Page, webapp, shots) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(webapp.base_url + "/")
    page.click("[data-new]")
    dlg = page.locator("dialog[open]")
    dlg.locator("[name=title]").fill("Workshop · cohort A")
    dlg.locator("[name=workshop]").fill("demo-workshop")
    dlg.locator("[name=folder]").fill("cohort-a")
    dlg.locator("[name=date]").fill("2026-10-27T18:00")
    dlg.locator(".detail-save-btn").click()

    expect(page.locator(".session-row.selected .row-title")).to_have_text("Workshop · cohort A")
    expect(page.locator(".detail-title h1")).to_have_text("Workshop · cohort A")
    expect(page.locator(".ready-row")).to_have_count(8)
    expect(page.locator(".status-line.ok")).to_contain_text("files on this PC")
    session_yaml = webapp.root / "sessions" / "demo-workshop" / "cohort-a" / "session.yaml"
    assert session_yaml.is_file()
    shot(page, shots / "story-02-sessions-1-desktop.png")

    # Mark the Zoom auto-update check done: it persists into session.yaml.
    page.locator(".ready-row.state-warn [data-action=confirm_zoom_update]").click()
    expect(page.locator(".ready-row.state-ok", has_text="Zoom auto-update")).to_have_count(1)
    assert "zoom_autoupdate_off: true" in session_yaml.read_text(encoding="utf-8")
