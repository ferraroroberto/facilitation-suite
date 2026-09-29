"""Story 16: two phones play the quiz (#52) — they join with the PIN and a nickname,
answer on the big tiles, see "Locked in" only once the server acked, survive a reload,
and see their result. One phone is 320 px wide on the WebSocket, the other polls.
The stage (#53) follows along: names pop into the lobby, the question shows no correct
answer and counts down from the server's deadline, the reveal marks it, the leaderboard ranks.
The first question has four 75-character answers under a corner camera (#84): the reveal shows
every line of every tile. The reveal, the leaderboard (that corner camera) and the podium (a camera
strip) keep their content out of the camera's box (#88)."""

from __future__ import annotations

import copy

from playwright.sync_api import Browser, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import LONG_ANSWERS, PLAYER_QUIZ, add_quiz_section

SMALL = {"viewport": {"width": 320, "height": 568}, "has_touch": True, "is_mobile": True}
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}

# On the stage, in canvas px (1920×1080): the answer tiles whose text does not fit their box, and the
# pieces of content (tiles, bars, leaderboard rows, podium steps) that reach into the camera zone
# [x0, y0, x1, y1] (fractions) — both must be empty.
CLIPPED = """() => [...document.querySelectorAll('.qz-tile')].filter((t) => {
  const x = t.querySelector('.qz-text'), a = t.getBoundingClientRect(), b = x.getBoundingClientRect();
  return t.scrollHeight > t.clientHeight + 1 || b.top < a.top - 1 || b.bottom > a.bottom + 1;
}).map((t) => t.dataset.choice)"""
IN_CAMERA = """(z) => {
  const c = document.querySelector('.stage-canvas').getBoundingClientRect(), s = c.width / 1920;
  const cam = { l: z[0] * 1920, t: z[1] * 1080, r: z[2] * 1920, b: z[3] * 1080 };
  return [...document.querySelectorAll(
    '.qz-tile, .qz-bar, .qz-board-title, .qz-row, .qz-top, .qz-block')].filter((e) => {
    const r = e.getBoundingClientRect(), l = (r.left - c.left) / s, t = (r.top - c.top) / s;
    return l < cam.r && l + r.width / s > cam.l && t < cam.b && t + r.height / s > cam.t;
  }).map((e) => e.className);
}"""


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
    section = copy.deepcopy(PLAYER_QUIZ)
    first = section["items"][1]
    first["options"].update(LONG_ANSWERS)
    first["profile"] = "camera_pip"  # a camera box in the top-right corner, as in a real session
    section["items"][-1]["profile"] = "camera_strip"  # the podium beside a camera strip
    add_quiz_section(folder, section)
    assert page.request.post(f"{base}/api/live/activate", data={"session": sid}).ok
    items = page.request.get(f"{base}/api/live").json()["plan"]["run"]["items"]
    lobby = next(n for n, it in enumerate(items) if it["id"] == "pq-lobby") + 1
    podium = next(n for n, it in enumerate(items) if it["id"] == "pq-podium") + 1
    assert page.request.post(f"{base}/api/actions/goto/{lobby}").ok
    pin = page.request.get(f"{base}/api/live").json()["state"]["quiz"]["pin"]

    errors: list[str] = []
    ana, bo = _phone(browser, SMALL, errors), _phone(browser, PHONE, errors)
    stage = _phone(browser, {"viewport": {"width": 1280, "height": 720}}, errors)  # what Zoom sees (#53)
    try:
        stage.goto(f"{base}/stage")
        # no public URL in this instance: the lobby says phones cannot join yet, instead of a dead QR
        expect(stage.locator("[data-qz-note]")).to_contain_text("not set up")
        _join(ana, f"{webapp.player_url}/play?pin={pin}", "Ana")
        _join(bo, f"{webapp.player_url}/play?pin={pin}&transport=poll", "Bo")
        expect(ana.locator("body")).to_have_attribute("data-transport", "ws")
        expect(bo.locator("body")).to_have_attribute("data-transport", "poll")
        quiz = page.request.get(f"{base}/api/live").json()["state"]["quiz"]
        assert [p["name"] for p in quiz["players"]] == ["Ana", "Bo"]
        expect(stage.locator(".qz-player")).to_have_text(["Ana", "Bo"])  # the names popped in

        assert page.request.post(f"{base}/api/actions/next").ok  # the first question
        for phone in (ana, bo):
            expect(phone.locator(".tile")).to_have_count(4)
            expect(phone.locator("[data-qindex]")).to_have_text("Question 1 of 2")
        # the stage: four tiles, nothing marks the correct answer before the reveal (the plan it gets holds it)
        expect(stage.locator(".qz-tile")).to_have_count(4)
        expect(stage.locator("[data-qz-index]")).to_have_text("Question 1 of 2")
        assert stage.locator("[data-correct], .qz-tile.correct, .qz-tile.wrong, .qz-bar").count() == 0
        # its countdown is the server's deadline, on the server's clock, within half a second
        live = page.request.get(f"{base}/api/live").json()
        shown = int(stage.get_attribute("[data-qz-clock]", "data-left-ms"))
        assert abs(shown - (live["state"]["quiz"]["deadline_ms"] - live["server_now"])) < 500
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
        # the reveal: bars per answer, the correct one marked by a check and a label, not by colour alone
        expect(stage.locator(".qz-tile.correct")).to_have_attribute("data-choice", "2")
        expect(stage.locator(".qz-tile.correct [data-correct]")).to_have_text("Correct")
        expect(stage.locator(".qz-bar-count")).to_have_text(["1", "1", "0", "0"])
        # every line of every 75-character answer shows, none under the camera (#84)
        stage.evaluate("document.fonts.ready")
        try:
            stage.wait_for_function(f"() => ({CLIPPED})().length === 0", timeout=3000)
        except PlaywrightTimeout:
            pass
        assert stage.evaluate(CLIPPED) == []
        zone = next(it for it in items if it["id"] == "pq-1")["zone"]
        assert zone and stage.evaluate(IN_CAMERA, zone) == []

        assert page.request.post(f"{base}/api/actions/next").ok  # leaderboard
        expect(ana.locator("[data-result]")).to_contain_text("#1")
        expect(bo.locator("[data-result]")).to_contain_text("#2")
        expect(stage.locator(".qz-row .qz-who")).to_have_text(["Ana", "Bo"])
        assert stage.evaluate(IN_CAMERA, zone) == []  # rank 1's score beside the camera, not under it (#88)

        assert page.request.post(f"{base}/api/actions/goto/{podium}").ok
        expect(stage.locator(".qz-step .qz-pname")).to_have_text(["Bo", "Ana"])  # 2nd, 1st
        strip = next(it for it in items if it["id"] == "pq-podium")["zone"]
        assert strip and stage.evaluate(IN_CAMERA, strip) == []
        assert errors == []
    finally:
        ana.context.close()
        bo.context.close()
        stage.context.close()
        page.request.post(f"{base}/api/live/deactivate")
