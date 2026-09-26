"""Story 15: the session's stage font and line thickness (picked on the
Sessions tab, used by the stage previews), then the plan list's multi-select —
Shift/Ctrl+click, the bulk panel, dragging the selection, deleting slides."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import yaml
from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session

# A font every Windows PC has, so the change is visible; else a renamed copy of the vendored one.
SYSTEM_FONT = Path("C:/Windows/Fonts/segoepr.ttf")
VENDORED = Path(__file__).resolve().parents[2] / "app" / "webapp" / "static" / "fonts" / "PatrickHand-Regular.ttf"


def _saved(folder: Path) -> dict:
    return yaml.safe_load((folder / "session.yaml").read_text(encoding="utf-8"))


def _until(check, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while not check():
        if time.time() > end:
            raise AssertionError("condition not met in time")
        time.sleep(0.1)


def test_stage_font_and_multi_select(page: Page, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "font-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    font = SYSTEM_FONT if SYSTEM_FONT.is_file() else Path(shutil.copy(VENDORED, webapp.root / "Synthetic-Regular.ttf"))

    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    # the native file dialog cannot open under test: answer the picker with the font
    page.route("**/api/pick", lambda route: route.fulfill(json={"path": str(font)}))
    page.goto(webapp.base_url + "/")

    # -- Sessions → Stage font: choose it, thicken its lines
    card = page.locator(".font-card")
    expect(card.locator(".card-head-meta")).to_have_text("Patrick Hand (theme)")
    card.get_by_role("button", name="Choose font…").click()
    expect(card.locator(".card-head-meta")).to_have_text(font.name)
    _until(lambda: (_saved(folder).get("font") or {}).get("file") == str(font))
    card.locator("input[type=range]").fill("2")
    expect(card.locator("output")).to_have_text("2 px")
    _until(lambda: _saved(folder)["font"]["stroke_px"] == 2)
    sample = card.locator(".st-question")
    expect(sample).to_have_css("-webkit-text-stroke-width", "2px")
    page.wait_for_function("() => document.fonts.check('64px \"Session Font\"')")
    card.scroll_into_view_if_needed()
    shot(page, shots / "story-15-font-1-sessions.png")
    ready = page.locator(".ready-row", has_text="Stage font")
    expect(ready).to_have_class("list-row ready-row state-ok")

    # -- Plan: the stage preview draws the question in the session font, lines thickened
    page.click("#tabPlan")
    page.locator('[data-item="act-kryptonite"]').click()  # saved as "Patrick Hand" = the theme font
    q = page.locator(".preview-frame .st-question")
    expect(q).to_contain_text("kryptonite")
    assert "Session Font" in q.evaluate("el => getComputedStyle(el).fontFamily")
    expect(q).to_have_css("-webkit-text-stroke-width", "2px")
    expect(page.locator(".ed-row", has_text="Question font").locator("select")).to_have_value("theme")

    # -- multi-select: click, Shift+click a range, Ctrl+click one out
    rows = page.locator(".item-row")
    page.locator('[data-item="slide-104"]').click()
    page.locator('[data-item="act-kryptonite"]').click(modifiers=["Shift"])
    expect(page.locator(".item-row.selected")).to_have_count(4)
    expect(page.locator(".plan-split .detail-title h1")).to_have_text("4 items selected")
    page.locator('[data-item="act-pairs"]').click(modifiers=["Control"])
    expect(page.locator(".item-row.selected")).to_have_count(3)
    expect(page.locator(".plan-split .detail-title p")).to_contain_text("2 slides · 1 activity")
    page.locator(".bulk-card .range-tab", has_text="Screen only").click()
    shot(page, shots / "story-15-font-2-multiselect.png")

    # drag the three onto the top half of an earlier slide: they land before it, in order
    page.locator('[data-item="slide-105"]').drag_to(page.locator('[data-item="slide-102"]'), target_position={"x": 40, "y": 4})
    order = rows.evaluate_all("rs => rs.map(r => r.dataset.item)")
    assert order[:6] == ["slide-101", "act-map", "slide-104", "slide-105", "act-kryptonite", "slide-102"], order
    expect(page.locator(".item-row.selected")).to_have_count(3)

    # Esc keeps one; a single imported slide can now be deleted from its editor
    page.locator('[data-item="slide-105"]').press("Escape")
    expect(page.locator(".item-row.selected")).to_have_count(1)
    page.locator('[data-item="slide-101"]').click()
    page.locator(".ed-tools [data-delete]").click()
    expect(page.locator(".dialog-message")).to_contain_text("stays in the deck")
    page.locator(".dialog-confirm").click()
    expect(page.locator('[data-item="slide-101"]')).to_have_count(0)

    # Shift+Down extends from the keyboard, Delete deletes both
    page.locator('[data-item="slide-107"]').click()
    page.locator('[data-item="slide-107"]').press("Shift+ArrowDown")
    expect(page.locator(".item-row.selected")).to_have_count(2)
    page.keyboard.press("Delete")
    expect(page.locator(".detail-dialog h2")).to_have_text("Delete 2 items?")
    page.locator(".dialog-confirm").click()
    expect(page.locator('[data-item="slide-107"], [data-item="slide-108"]')).to_have_count(0)

    page.locator(".dirty-bar [data-save]").click()
    expect(page.locator(".dirty-bar")).to_have_count(0)
    saved = _saved(folder)
    ids = [i.get("id") for s in saved["sections"] for i in s["items"]]
    assert "slide-101" not in ids and "slide-107" not in ids and "slide-108" not in ids
    welcome = saved["sections"][0]["items"]
    assert [i["id"] for i in welcome] == ["act-map", "slide-104", "slide-105", "act-kryptonite", "slide-102", "act-weather", "slide-103"]
    assert {i["profile"] for i in welcome[1:4]} == {"screen_only"}
    # the deleted slides are still in the deck, to add back
    deck = page.request.get(f"{webapp.base_url}/api/sessions/{sid}/slides").json()
    assert {101, 107, 108} <= {s["slide_id"] for s in deck["slides"]}
