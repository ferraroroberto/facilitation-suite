"""Story 16: two phones play the quiz (#52) — they join with the PIN and a nickname,
answer on the big tiles, see "Locked in" only once the server acked, survive a reload,
and see their result. One phone is 320 px wide on the WebSocket, the other polls."""

from __future__ import annotations

from playwright.sync_api import Browser, Page, expect

from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import add_quiz_section

SMALL = {"viewport": {"width": 320, "height": 568}, "has_touch": True, "is_mobile": True}
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}


def _phone(browser: Browser, spec: dict, errors: list[str]) -> Page:
    page = browser.new_context(**spec).new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page


def _join(page: Page, url: str, nickname: str) -> None:
    page.goto(url)
    page.fill("#nickname", nickname)
    page.click("#joinBtn")
    expect(page.locator("[data-for=wait]")).to_contain_text("You're in")


def test_two_phones_play_a_question(page: Page, browser: Browser, webapp) -> None:
    base = webapp.base_url
    folder = webapp.root / "sessions" / "demo" / "quiz-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    add_quiz_section(folder)
    assert page.request.post(f"{base}/api/live/activate", data={"session": sid}).ok
    items = page.request.get(f"{base}/api/live").json()["plan"]["run"]["items"]
    lobby = next(n for n, it in enumerate(items) if it["id"] == "pq-lobby") + 1
    assert page.request.post(f"{base}/api/actions/goto/{lobby}").ok
    pin = page.request.get(f"{base}/api/live").json()["state"]["quiz"]["pin"]

    errors: list[str] = []
    ana, bo = _phone(browser, SMALL, errors), _phone(browser, PHONE, errors)
    try:
        _join(ana, f"{webapp.player_url}/play?pin={pin}", "Ana")
        _join(bo, f"{webapp.player_url}/play?pin={pin}&transport=poll", "Bo")
        expect(ana.locator("body")).to_have_attribute("data-transport", "ws")
        expect(bo.locator("body")).to_have_attribute("data-transport", "poll")
        quiz = page.request.get(f"{base}/api/live").json()["state"]["quiz"]
        assert [p["name"] for p in quiz["players"]] == ["Ana", "Bo"]

        assert page.request.post(f"{base}/api/actions/next").ok  # the first question
        for phone in (ana, bo):
            expect(phone.locator(".tile")).to_have_count(4)
            expect(phone.locator("[data-qindex]")).to_have_text("Question 1 of 2")
        # 320 px: the four tiles fit, nothing scrolls sideways
        assert ana.evaluate("document.documentElement.scrollWidth") <= 320
        assert ana.locator(".tile").first.bounding_box()["width"] >= 120

        ana.locator('.tile[data-choice="2"]').click()  # right
        bo.locator('.tile[data-choice="1"]').click()  # wrong
        for phone in (ana, bo):
            expect(phone.locator("[data-answer-status]")).to_contain_text("Locked in")
        ana.reload()  # the same player, still locked in
        expect(ana.locator("[data-answer-status]")).to_contain_text("Locked in")
        expect(ana.locator("[data-locked-shape]")).to_have_attribute("data-choice", "2")

        assert page.request.post(f"{base}/api/actions/next").ok  # reveal
        expect(ana.locator("[data-result]")).to_contain_text("Correct")
        expect(ana.locator("[data-result-detail]")).to_contain_text("points · #1")
        expect(bo.locator("[data-result]")).to_contain_text("Not this time")
        quiz = page.request.get(f"{base}/api/live").json()["state"]["quiz"]
        assert quiz["distribution"] == [1, 1, 0, 0] and quiz["answered_count"] == 2

        assert page.request.post(f"{base}/api/actions/next").ok  # leaderboard
        expect(ana.locator("[data-result]")).to_contain_text("#1")
        expect(bo.locator("[data-result]")).to_contain_text("#2")
        assert errors == []
    finally:
        ana.context.close()
        bo.context.close()
        page.request.post(f"{base}/api/live/deactivate")
