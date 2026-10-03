"""Story 4: plan the session — edit an activity, give it its own timer, notes and a
title, break its question over two lines, keep its answers verbatim, duplicate it, reorder, skip, fold the
sections, add one with a breakout card in it, save. A preview drawn while its host has zero size (a
collapsed section, an inactive tab) is re-fit once it becomes visible (#103): a word cloud's words and a
quiz's answer tiles. Picking another OBS profile re-lays a quiz preview out around its camera box (#148)."""

from __future__ import annotations

from pathlib import Path

import yaml
from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot, small_targets
from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import LONG_ANSWERS, add_quiz_section

# On the stage (matches test_story_16_quiz_player.py's CLIPPED): a tile whose text overflows its box.
CLIPPED = """() => [...document.querySelectorAll('.qz-tile')].filter((t) => {
  const x = t.querySelector('.qz-text'), a = t.getBoundingClientRect(), b = x.getBoundingClientRect();
  return t.scrollHeight > t.clientHeight + 1 || b.top < a.top - 1 || b.bottom > a.bottom + 1;
}).map((t) => t.dataset.choice)"""
# In the Plan tab's preview: the quiz pieces (the question's own lines, the status row, the tiles)
# that reach into the dashed camera box of the item's profile (#148) — must be empty.
UNDER_GUIDE = """() => {
  const guide = document.querySelector('.preview-frame .st-guide');
  if (!guide) return [];
  const g = guide.getBoundingClientRect(), out = [];
  for (const e of document.querySelectorAll('.preview-frame :is(.st-question, .qz-status > :not([hidden]), .qz-tile)')) {
    let rects = [e.getBoundingClientRect()];
    if (e.matches('.st-question')) { const r = document.createRange(); r.selectNodeContents(e); rects = [...r.getClientRects()]; }
    if (rects.some((r) => r.width > 0 && r.left < g.right && r.right > g.left && r.top < g.bottom && r.bottom > g.top)) out.push(e.className);
  }
  return out;
}"""


def _hide_and_rebuild(page: Page, field) -> None:
    """Zero the stage preview's host — as a collapsed section's or an inactive tab's [hidden]
    pane leaves it — and rebuild it through the real edit -> markDirty -> updatePreview path
    while it stays hidden (a bump and, still hidden, its revert — so `field`'s saved value is
    unchanged), then reveal it again."""
    page.evaluate("""() => {
        const frame = document.querySelector('.preview-frame');
        frame.dataset.savedDisplay = frame.style.display;
        frame.style.display = 'none';
    }""")
    field.evaluate("""(el) => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        const was = el.value;
        setter.call(el, `${was} `);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        setter.call(el, was);
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    page.evaluate("""() => {
        const frame = document.querySelector('.preview-frame');
        frame.style.display = frame.dataset.savedDisplay || '';
    }""")


def test_edit_the_plan_and_save_it(page: Page, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "plan-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    add_quiz_section(folder, {
        "id": "sec-refit", "name": "Refit check", "minutes": 5,
        "items": [{"kind": "activity", "id": "act-refit-quiz", "type": "quiz", "question": "Refit?",
                   "options": {**LONG_ANSWERS, "correct": "1", "time_limit": 20}}],
    }, at=5)  # appended after "Closing" — doesn't shift any of the other sections' positions
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    page.goto(webapp.base_url + "/")
    page.click("#tabPlan")
    expect(page.locator(".sec-row")).to_have_count(6)
    # #183: Plan spans the window at 1440px (only the Sessions list holds the 772px measure)
    assert page.locator("#panePlan").bounding_box()["width"] > 1000

    # fold every section, open them again
    page.locator("[data-fold=collapse]").click()
    expect(page.locator(".item-row")).to_have_count(0)
    page.locator("[data-fold=expand]").click()
    expect(page.locator(".item-row")).to_have_count(19)

    # the closing word cloud: a title, a question on two lines, font size, its own 90 s timer, notes
    page.locator(".item-row", has_text="What do you take away today?").click()
    page.locator(".ed-row", has_text="Title").locator("input").fill("Take-home word")
    page.get_by_placeholder("What did you learn about this group?").fill(r"One word\nyou take home")
    page.locator(".ed-row", has_text="Question font").locator("input").fill("88")
    page.locator(".timer-box .toggle").click()
    page.locator(".dur-input").fill("1:30")
    page.get_by_label("Notes").fill("Read the top three aloud.")
    expect(page.locator(".dirty-bar")).to_be_visible()
    assert small_targets(page) == []  # #167: the plan and its editor, every control reaches 44px
    # #167: the 44px fields keep a real 1px boundary (an inset shadow reads as none: COLOR-03)
    assert set(page.eval_on_selector_all(".sec-min input, .input:not(textarea), .select-native", "els => els.map(e => getComputedStyle(e).borderTopWidth)")) == {"1px"}
    # #167: a glyph in a button label is on the icons.size inline step (16px), not the label's 1em
    assert set(page.eval_on_selector_all(".button-surface .icon, .button-ghost .icon, .button-tint .icon", "els => els.map(e => e.getBoundingClientRect().width).filter(w => w > 0)")) == {16}
    # #167: --muted is under 4.5:1 on a selected (accent-soft) row in both themes and on the dark section band,
    # so the secondary text there is the primary ink -- the same colour as the name beside it
    theme = page.evaluate("document.documentElement.dataset.theme")
    color = lambda sel: page.locator(sel).first.evaluate("e => getComputedStyle(e).color")  # noqa: E731
    for mode in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", mode)
        assert color(".item-row.selected .num") == color(".item-row.selected .item-title")
    assert color(".sec-row .sec-meta") == color(".sec-row .sec-name")  # the dark band, the theme left on
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    expect(page.locator(".preview-frame .st-question")).to_have_js_property("innerHTML", "One word<br>you take home")
    expect(page.locator(".item-row.selected .item-title")).to_have_text("Take-home word")
    # answers verbatim: the preview's sample "saying yes to everything" stops being split into words
    whole = page.locator(".preview-frame .wc-text", has_text="saying yes to everything")
    expect(page.locator(".preview-frame .wc-text", has_text="everything")).to_have_text("everything")
    page.get_by_label("How answers become words").select_option("verbatim")
    expect(whole).to_be_visible()

    # duplicate it: the copy follows it and is selected
    page.locator(".ed-tools [data-dup]").click()
    expect(page.locator(".sec-row", has_text="Closing")).to_contain_text("3 items")

    # skip the slide after it, and move the closing section's slide to the top
    page.locator(".item-row", has_text="Thank you!").click()
    page.locator(".ed-row", has_text="In this session").locator(".toggle").click()
    page.locator("[data-up]").click()
    page.locator("[data-up]").click()

    # a new section right after "Working agreement", from its add menu
    page.locator(".add-row").nth(3).click()
    page.locator(".row-menu-item", has_text="Section after this one").click()
    page.fill("#f-name", "Energiser")
    page.locator(".detail-save-btn").click()
    expect(page.locator(".sec-row").nth(4)).to_contain_text("Energiser")
    # a breakout card in it: the round under its title, a ten-minute clock
    page.locator(".add-row").nth(4).click()
    page.locator(".row-menu-item", has_text="Breakout").click()
    page.locator("#panePlan .ed-row", has_text="Rooms").locator(".range-tab", has_text="Groups of 4 · A").click()
    expect(page.locator(".item-row.selected")).to_contain_text("Groups of 4 · A")
    expect(page.locator(".preview-frame .st-break-title")).to_have_text("Breakout rooms")
    expect(page.locator(".preview-frame .st-sub")).to_have_text("Groups of 4 · A")  # no shuffle yet: no room count
    expect(page.locator(".preview-frame [data-clock]")).to_have_text("10:00")
    page.locator(".item-row", has_text="Take-home word").first.click()
    expect(page.locator(".preview-frame .wc-word").first).to_be_visible()  # sample answers drawn

    # #103: a preview drawn while its host has zero size never lays out — until it is opened
    # again. Rebuild the word cloud (and, for a quiz, its answer tiles) while its host is hidden:
    # once revealed, every word — and every tile's text — must already be there and fit, with no
    # further action.
    word_count = page.locator(".preview-frame .wc-word").count()
    assert word_count > 0
    _hide_and_rebuild(page, page.locator(".ed-row", has_text="Title").locator("input").first)
    expect(page.locator(".preview-frame .wc-word")).to_have_count(word_count)

    page.locator(".item-row", has_text="Refit?").first.click()
    expect(page.locator(".preview-frame .qz-tile")).to_have_count(4)
    assert page.evaluate(CLIPPED) == []
    _hide_and_rebuild(page, page.locator(".ed-row", has_text="Title").locator("input").first)
    expect(page.locator(".preview-frame .qz-tile")).to_have_count(4)
    assert page.evaluate(CLIPPED) == []
    # #148: picking another OBS profile re-lays the quiz preview out around that profile's camera box
    for label, profile in (("Camera strip", "camera_strip"), ("Camera PiP", "camera_pip"), ("Screen only", "screen_only")):
        page.locator("#panePlan .ed-row", has_text="Camera layout").locator(".range-tab", has_text=label).click()
        expect(page.locator(".preview-frame .st-item")).to_have_attribute("data-profile", profile)
        page.wait_for_function(f"() => ({UNDER_GUIDE})().length === 0 && ({CLIPPED})().length === 0")

    page.locator(".item-row", has_text="Take-home word").first.click()
    expect(page.locator(".preview-frame .wc-word").first).to_be_visible()
    shot(page, shots / "story-04-plan-1-desktop.png")
    page.locator(".ed-save").click()
    expect(page.locator(".dirty-bar")).to_have_count(0)

    saved = yaml.safe_load((Path(folder) / "session.yaml").read_text(encoding="utf-8"))
    assert [s["name"] for s in saved["sections"]] == [
        "Welcome", "Personal readme", "Break", "Working agreement", "Energiser", "Closing", "Refit check"]
    assert [(i["kind"], i["options"], i["timer"]["seconds"]) for i in saved["sections"][4]["items"]] == [("breakout", {"round": "g4a"}, 600)]
    closing = saved["sections"][-2]["items"]
    assert [i.get("slide_id") or i["id"] for i in closing][:2] == [110, "act-takeaway"]
    assert closing[0]["include"] is False
    act, copy = closing[1], closing[2]
    assert act["title"] == "Take-home word"
    assert act["question"] == r"One word\nyou take home"
    assert act["font"]["size_px"] == 88
    assert act["notes"] == "Read the top three aloud."
    assert act["options"] == {"terms": "verbatim"}
    assert act["timer"] == {"enabled": True, "seconds": 90, "start": "with_capture", "show_on": "stage", "end": "stop_capture"}
    assert copy["id"] != act["id"] and {k: v for k, v in copy.items() if k != "id"} == {k: v for k, v in act.items() if k != "id"}
    # untouched items keep no timer: timers are decided item by item
    assert "timer" not in saved["sections"][0]["items"][0]
