"""Story 2: create a session folder from the app, see it in the ledger with its readiness list; it
starts from Settings → Stage defaults, can override them and reset to them (#110). An opened
session takes the whole pane; X, Esc and Back return to the list, /#sessions/<id> reopens it (#150).
Its details edit inline in its Session settings card and survive a reload (#151)."""

from __future__ import annotations

import re

import yaml
from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot


def test_create_a_session_and_see_it_ready_list(page: Page, webapp, shots) -> None:
    defaults = webapp.base_url + "/api/settings/defaults"
    assert page.request.put(defaults, data={"stage": {"font": {"family": "Georgia", "weight": 700}}}).ok
    # #211: the timer colours are a global default too — set here in Settings, then copied into a new session
    page.goto(webapp.base_url + "/#settings/stage")
    default_card = page.locator(".defaults-font-card")
    expect(default_card.locator("[data-timer-color]")).to_have_count(4)
    expect(default_card.locator("[data-timer-color=running]")).to_have_value("#00a44e")
    expect(default_card.locator("[data-timer-reset]")).to_be_disabled()
    default_card.locator("[data-timer-color=running]").fill("#1565c0")
    expect(default_card.locator("[data-timer-reset]")).to_be_enabled()
    expect(default_card.locator(".st-pill.running")).to_have_css("background-color", "rgb(21, 101, 192)")
    for _ in range(50):  # the change saves on its own
        saved_font = page.request.get(defaults).json()["stage"]["font"] or {}
        if saved_font.get("timer_running") == "#1565c0":
            break
        page.wait_for_timeout(100)
    assert saved_font.get("timer_running") == "#1565c0" and saved_font["family"] == "Georgia"
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(webapp.base_url + "/")
    page.click("[data-new]")
    dlg = page.locator("dialog[open]")
    dlg.locator("[name=title]").fill("Workshop · cohort A")
    dlg.locator("[name=workshop]").fill("demo-workshop")
    dlg.locator("[name=folder]").fill("cohort-a")
    dlg.locator("[name=date]").fill("2026-10-27T18:00")
    dlg.locator(".detail-save-btn").click()

    # the new session opens full screen: the list is gone, the nav stays, the URL deep-links it
    expect(page.locator(".detail-title h1")).to_have_text("Workshop · cohort A")
    expect(page.locator(".sessions-list")).to_be_hidden()
    expect(page.locator("#tabSessions")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/#sessions/[0-9a-f]+$"))
    deep_link = page.url
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
    card = page.locator(".session-settings-card")
    state = card.locator("[data-look-state]")
    expect(state).to_have_text("Using the default")
    expect(card.locator("[data-lettering]")).to_have_text("Georgia (serif)")
    assert yaml.safe_load(session_yaml.read_text(encoding="utf-8"))["font"]["family"] == "Georgia"
    assert yaml.safe_load(session_yaml.read_text(encoding="utf-8"))["font"]["timer_running"] == "#1565c0"  # #211
    card.locator("select[aria-label='Title font']").select_option("system-ui")
    expect(state).to_have_text("Overridden")
    card.locator("[data-look-reset]").click()
    expect(state).to_have_text("Using the default")
    expect(card.locator("[data-look-reset]")).to_have_count(0)
    saved = yaml.safe_load(session_yaml.read_text(encoding="utf-8"))["font"]
    assert saved["family"] == "Georgia" and saved["weight"] == 700 and saved["timer_running"] == "#1565c0"
    assert page.request.put(defaults, data={"stage": {"font": None}}).ok  # the theme's lettering for the stories after this

    # #150: X closes to the list (the session stays selected), Esc and Back do too, a deep link reopens it
    page.locator("[data-close-session]").click()
    expect(page.locator(".sessions-detail")).to_be_hidden()
    expect(page.locator(".session-row.selected .row-title")).to_have_text("Workshop · cohort A")
    expect(page).to_have_url(webapp.base_url + "/")
    page.locator(".session-row.selected").click()
    expect(page.locator(".ready-row")).to_have_count(8)
    page.keyboard.press("Escape")
    expect(page.locator(".sessions-list")).to_be_visible()
    page.locator(".session-row.selected").click()
    expect(page.locator(".sessions-detail")).to_be_visible()
    page.go_back()
    expect(page.locator(".sessions-list")).to_be_visible()
    expect(page.locator(".sessions-detail")).to_be_hidden()
    page.click("#tabPlan")
    expect(page.locator("#panePlan .home-head .status")).to_have_text("0 sections · 0:00")  # the selected session
    page.goto(deep_link)
    page.reload()
    expect(page.locator(".detail-title h1")).to_have_text("Workshop · cohort A")
    expect(page.locator(".sessions-list")).to_be_hidden()

    # #151: the details edit inline in Session settings, each saved to session.yaml on its own
    settings = page.locator(".session-settings-card")
    title = settings.locator("[data-meta=title]")
    title.fill("Workshop · cohort A (rehearsal)")
    title.press("Enter")
    expect(page.locator(".detail-title h1")).to_have_text("Workshop · cohort A (rehearsal)")
    settings.locator("[data-meta=date]").fill("2026-11-03T09:30")
    settings.locator("[data-meta=date]").press("Enter")
    settings.locator("[data-meta=duration_minutes]").fill("90")
    settings.locator("[data-meta=duration_minutes]").press("Tab")
    summary = page.locator(".detail-title p")
    expect(summary).to_contain_text("Tue 3 Nov · 09:30–11:00 · 1:30 planned")
    hint = settings.locator(".font-sample .st-hint")  # the stage preview says the new words at once
    settings.locator("[data-meta=language]").select_option("es")
    expect(hint).to_have_text("Escribe tu respuesta en el chat")
    expect(summary).to_contain_text("Español on stage")
    settings.locator("[data-meta=chat_hint]").fill("Contesta en el chat")
    settings.locator("[data-meta=chat_hint]").press("Enter")
    expect(hint).to_have_text("Contesta en el chat")
    saved = yaml.safe_load(session_yaml.read_text(encoding="utf-8"))
    assert saved["title"] == "Workshop · cohort A (rehearsal)" and saved["duration_minutes"] == 90
    assert saved["language"] == "es" and saved["chat_hint"] == "Contesta en el chat"
    assert str(saved["date"]).startswith("2026-11-03")
    page.reload()
    expect(title).to_have_value("Workshop · cohort A (rehearsal)")
    expect(settings.locator("[data-meta=date]")).to_have_value("2026-11-03T09:30")
    expect(settings.locator("[data-meta=duration_minutes]")).to_have_value("90")
    expect(settings.locator("[data-meta=language]")).to_have_value("es")
    expect(hint).to_have_text("Contesta en el chat")
