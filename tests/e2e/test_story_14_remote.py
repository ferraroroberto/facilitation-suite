"""Story 14: the phone remote — what is on stage, what is next, the session
clock; Next, Start capture (the timer starts with it), answers counting up,
blackout; the chat to hide a message from; the breakout rooms to read out;
and what an unpaired phone sees."""

from __future__ import annotations

import time
import uuid

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session, demo_person

PHONE = {"width": 390, "height": 844}


def _state(page: Page, base: str) -> dict:
    return page.request.get(f"{base}/api/live").json()["state"]


def test_phone_remote(page: Page, browser: Browser, webapp, shots) -> None:
    base = webapp.base_url
    folder = webapp.root / "sessions" / "demo" / "remote-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    assert page.request.post(f"{base}/api/sessions/{sid}/groups/shuffle", data={"seed": 7}).ok
    assert page.request.post(f"{base}/api/live/activate", data={"session": sid}).ok
    assert page.request.post(f"{base}/api/live/action", data={"action": "clock_start"}).ok

    page.set_viewport_size(PHONE)
    page.goto(f"{base}/remote")
    sub = page.locator("[data-r-sub]")
    expect(sub).to_contain_text("Slide 1 of")
    expect(page.locator("[data-r-next]")).to_have_text("Where are you joining from?")
    expect(page.locator("[data-r-capture]")).to_be_hidden()  # a slide has nothing to capture
    expect(page.locator("[data-r-prev]")).to_be_disabled()

    page.locator("[data-r-nextbtn]").click()  # to the map activity
    expect(sub).to_contain_text("Item 2 of")
    expect(page.locator("[data-r-capture]")).to_contain_text("Start capture")
    for _ in range(5):  # on to the kryptonite word cloud (item 8)
        page.locator("[data-r-nextbtn]").click()
        time.sleep(0.15)
    page.locator("[data-r-nextbtn]").click()
    expect(page.locator("[data-r-title]")).to_contain_text("kryptonite")
    page.locator("[data-r-capture]").click()
    expect(page.locator("[data-r-capture]")).to_contain_text("Stop capture")
    expect(sub).to_contain_text("capturing")
    expect(page.locator("[data-r-timer]")).to_have_class("r-timer running")  # starts with the capture

    rows = [{"sender": demo_person(i), "text": t, "time": time.strftime("%H:%M")}
            for i, t in enumerate(["meetings", "perfectionism", "meetings", "notifications", "can you hear me?", "tiredness"])]
    assert page.request.post(f"{base}/api/chat/messages", data={"batch": uuid.uuid4().hex, "source": "zoom", "messages": rows}).ok
    expect(page.locator("[data-r-count]")).to_contain_text("6 answers", timeout=10000)
    expect(page.locator("[data-r-elapsed]")).not_to_have_text("--:--")
    page.wait_for_function("() => document.fonts.ready.then(() => true)")
    time.sleep(0.6)  # let the cloud settle
    shot(page, shots / "story-14-remote-1-live.png")

    # the chat: "can you hear me?" is not an answer — tap it to hide it
    page.click("#tabChat")
    msg = page.locator(".r-msg", has_text="can you hear me?")
    msg.click()
    expect(msg).to_have_class("r-msg hidden-msg")
    page.click("#tabLive")
    expect(page.locator("[data-r-count]")).to_contain_text("5 answers")
    page.click("#tabChat")
    shot(page, shots / "story-14-remote-2-chat.png")

    page.click("#tabGroups")
    expect(page.locator(".r-rooms li").first).to_contain_text("Room 1")
    page.locator(".range-tab", has_text="Groups of 4 · A").click()
    expect(page.locator(".range-tab.active")).to_contain_text("Groups of 4 · A")
    shot(page, shots / "story-14-remote-3-groups.png")

    page.click("#tabLive")
    page.locator("[data-r-capture]").click()
    expect(page.locator("[data-r-capture]")).to_contain_text("Reopen capture")
    page.locator("[data-r-blackout]").click()
    expect(sub).to_contain_text("blackout")
    assert _state(page, base)["blackout"] is True
    page.locator("[data-r-blackout]").click()
    expect(sub).not_to_contain_text("blackout")

    # a phone that has not opened the pairing link is told how to pair
    stranger = browser.new_context(viewport=PHONE)
    other = stranger.new_page()
    other.route("**/api/live", lambda route: route.fulfill(
        status=401, content_type="application/json",
        body='{"error": {"code": "remote_token_required", "message": "This device is not paired — open the phone link from Settings on the PC"}}'))
    other.goto(f"{base}/remote")
    expect(other.locator(".empty-state")).to_contain_text("Settings → Phone remote")
    expect(other.locator(".tabs")).to_be_hidden()
    shot(other, shots / "story-14-remote-4-unpaired.png")
    stranger.close()
