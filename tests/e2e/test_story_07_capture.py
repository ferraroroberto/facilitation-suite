"""Story 7: capture an activity — Space starts it, simulated answers grow the
word cloud on the stage, a message is hidden, Space stops it and freezes it."""

from __future__ import annotations

import json
import time

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session


def test_capture_a_word_cloud_and_freeze_it(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "capture-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    page.locator(".p-thumb", has_text="kryptonite").click()
    expect(page.locator(".p-item .p-label")).to_have_text("Capture")

    stage_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    stage = stage_ctx.new_page()
    stage.goto(f"{webapp.base_url}/stage")
    expect(stage.locator(".st-question")).to_contain_text("kryptonite")

    page.keyboard.press("Space")
    expect(page.locator("[data-captoggle]")).to_contain_text("Stop capture")
    expect(page.locator("[data-tstate]")).to_have_text("running")  # the timer starts with the capture

    resp = page.request.post(f"{webapp.base_url}/api/chat/simulate",
                             data={"kind": "burst", "count": 12, "answers": ["meetings", "perfectionism", "hello"]})
    assert resp.ok
    expect(page.locator("[data-capcounts]")).to_contain_text("12 answers", timeout=20000)
    expect(stage.locator(".wc-word", has_text="perfectionism 2")).to_be_visible()
    expect(stage.locator(".wc-word", has_text="hello 3")).to_be_visible()
    expect(stage.locator(".st-count")).to_contain_text("12")

    # "hello 3" is noise: click it in the presenter's chat to hide it
    page.locator(".p-msg", has_text="hello 3").click()
    expect(page.locator(".p-msg.hidden-msg")).to_have_count(1)
    expect(page.locator("[data-capcounts]")).to_contain_text("11 answers from")
    expect(page.locator("[data-capcounts]")).to_contain_text("1 hidden")
    expect(stage.locator(".wc-word", has_text="hello 3")).to_have_count(0)
    shot(page, shots / "story-07-capture-1-presenter.png")
    shot(stage, shots / "story-07-capture-2-stage.png")

    page.keyboard.press("Space")
    expect(page.locator("[data-captoggle]")).to_contain_text("Reopen capture")
    frozen = json.loads((folder / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert frozen["result"]["answers"] == 11
    assert sum(a["hidden"] for a in frozen["answers"]) == 1
    png = folder / "live" / "captures" / "act-kryptonite.png"
    deadline = time.time() + 30
    while not png.is_file() and time.time() < deadline:
        time.sleep(0.5)
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    stage_ctx.close()
