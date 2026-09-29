"""Story 2: create a session folder from the app, see it in the ledger with its readiness list; it
starts from Settings → Stage defaults, can override them and reset to them (#110)."""

from __future__ import annotations

import yaml
from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot


def test_create_a_session_and_see_it_ready_list(page: Page, webapp, shots) -> None:
    defaults = webapp.base_url + "/api/settings/defaults"
    assert page.request.put(defaults, data={"stage": {"font": {"family": "Georgia", "weight": 700}}}).ok
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

    # #110: the new session starts from the default lettering; a change overrides it, Reset takes it back
    card = page.locator(".font-card")
    state = card.locator("[data-look-state]")
    expect(state).to_have_text("Using the default")
    expect(card.locator(".card-head-meta")).to_have_text("Georgia (serif)")
    assert yaml.safe_load(session_yaml.read_text(encoding="utf-8"))["font"]["family"] == "Georgia"
    card.locator("select[aria-label='Title font']").select_option("system-ui")
    expect(state).to_have_text("Overridden")
    card.locator("[data-look-reset]").click()
    expect(state).to_have_text("Using the default")
    expect(card.locator("[data-look-reset]")).to_have_count(0)
    saved = yaml.safe_load(session_yaml.read_text(encoding="utf-8"))["font"]
    assert saved["family"] == "Georgia" and saved["weight"] == 700
    assert page.request.put(defaults, data={"stage": {"font": None}}).ok  # the theme's lettering for the stories after this
