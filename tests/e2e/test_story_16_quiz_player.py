"""Story 16: two phones play the quiz (#52) — they join with the PIN and a nickname,
answer on the big tiles, see "Locked in" only once the server acked, survive a reload,
and see their result. One phone is 320 px wide on the WebSocket, the other polls.
The stage (#53) follows along: names pop into the lobby, the question shows no correct
answer and counts down from the server's deadline, the reveal marks it, the leaderboard ranks.
The first question has four 75-character answers under a corner camera (#84): the reveal shows
every line of every tile. The reveal, the leaderboard (that corner camera) and the podium (a camera
strip) keep their content out of the camera's box (#88). The phones show that 120-character
question and the four answers' texts in their tiles (#90), every word of them at 320 and 390 px,
light and dark, and after the reveal the right answer's text. The podium reveals one place per
Next (#89): 3rd, 2nd, 1st, and the fourth Next moves on. Switched to Spanish on the leaderboard,
the stage and a reloaded phone say it in Spanish (#91). A stage reloaded on a lobby that already has
players shows every name (#111)."""

from __future__ import annotations

import copy

from playwright.sync_api import Browser, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import LONG_ANSWERS, LONG_QUESTION, PLAYER_QUIZ, add_quiz_section

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
# On a phone: how wide the page is, and which tiles (or the question) cut their text (#90).
PHONE_FIT = """() => ({ width: document.documentElement.scrollWidth,
  clipped: [...document.querySelectorAll('.tile, [data-qtext]')].filter((e) =>
    e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1).map((e) => e.dataset.choice || 'question') })"""
# The reveal's bars: which shape icons spill outside their bar, even at 0 votes (a bar at its
# floor height, #93) — must be empty. getBoundingClientRect, not scrollHeight: overflow:hidden on
# the bar does not grow its scrollHeight for a flex child that overflows it.
BAR_ICON_CLIPPED = """() => [...document.querySelectorAll('.qz-bar')].filter((b) => {
  const fill = b.querySelector('.qz-bar-fill'), shape = b.querySelector('.qz-shape');
  const f = fill.getBoundingClientRect(), s = shape.getBoundingClientRect();
  return s.top < f.top - 1 || s.bottom > f.bottom + 1 || s.left < f.left - 1 || s.right > f.right + 1;
}).map((b) => b.dataset.bar)"""
# The countdown ring's digits (a Range around the text, not the centring box, which always fills
# the ring) stay clear of the ring's own stroke even at 3 digits (a 120/240 s limit, #93): every
# corner of the text is within the circle's radius, minus half the stroke's width.
COUNTDOWN_FITS = """() => {
  const clock = document.querySelector('.qz-clock');
  const svg = clock.querySelector('svg'), track = clock.querySelector('.qz-track');
  const secs = clock.querySelector('[data-qz-secs]');
  const sr = svg.getBoundingClientRect(), scale = sr.width / 120; // the viewBox is 0 0 120 120
  const cx = sr.left + 60 * scale, cy = sr.top + 60 * scale;
  const strokeW = parseFloat(getComputedStyle(track).strokeWidth) * scale;
  const inradius = 54 * scale - strokeW / 2;
  const range = document.createRange();
  range.selectNodeContents(secs);
  const t = range.getBoundingClientRect();
  const corners = [[t.left, t.top], [t.right, t.top], [t.left, t.bottom], [t.right, t.bottom]];
  return corners.every(([x, y]) => Math.hypot(x - cx, y - cy) <= inradius);
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
    first["options"]["time_limit"] = 240  # a 3-digit countdown (Kahoot allows up to 240s, #93)
    first["question"] = LONG_QUESTION
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
        # a third player, straight through the player API, who never answers: the podium's 3rd (#89)
        cy = page.request.post(f"{webapp.player_url}/play/api/join", data={"pin": pin, "nickname": "Cy"})
        assert cy.ok and cy.json()["state"] == "joined"
        # a stage (re)opened on a lobby that already has players shows every name, not "+3 more" (#111)
        stage.reload()
        expect(stage.locator(".qz-player:visible")).to_have_text(["Ana", "Bo", "Cy"])

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
        # the 240 s limit's 3-digit countdown fits inside the ring, not spilling past its edge (#93)
        expect(stage.locator("[data-qz-secs]")).to_have_text("240")
        assert stage.evaluate(COUNTDOWN_FITS)
        # 320 px: the four tiles fit, nothing scrolls sideways
        assert ana.evaluate("document.documentElement.scrollWidth") <= 320
        assert ana.locator(".tile").first.bounding_box()["width"] >= 120
        # the question and every answer's text, whole, on both phones, in light and in dark (#90)
        for phone, width in ((ana, 320), (bo, 390)):
            expect(phone.locator("[data-qtext]")).to_have_text(LONG_QUESTION)
            expect(phone.locator(".tile .tile-text")).to_have_text(list(LONG_ANSWERS.values()))
            for theme in ("light", "dark"):
                phone.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
                assert phone.evaluate(PHONE_FIT) == {"width": width, "clipped": []}, (width, theme)

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
        for phone in (ana, bo):  # the right answer's text, next to the result
            expect(phone.locator("[data-right] .play-right-text")).to_have_text([LONG_ANSWERS["answer_2"]])
        quiz = page.request.get(f"{base}/api/live").json()["state"]["quiz"]
        assert quiz["distribution"] == [1, 1, 0, 0] and quiz["answered_count"] == 2
        # the reveal: bars per answer, the correct one marked by a check and a label, not by colour alone
        expect(stage.locator(".qz-tile.correct")).to_have_attribute("data-choice", "2")
        expect(stage.locator(".qz-tile.correct [data-correct]")).to_have_text("Correct")
        expect(stage.locator(".qz-bar-count")).to_have_text(["1", "1", "0", "0"])
        # every bar's shape icon shows whole, even at 0 votes (a bar at its floor height, #93)
        assert stage.evaluate(BAR_ICON_CLIPPED) == []
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
        expect(stage.locator(".qz-row .qz-who")).to_have_text(["Ana", "Bo", "Cy"])
        assert stage.evaluate(IN_CAMERA, zone) == []  # rank 1's score beside the camera, not under it (#88)

        # the session switched to Spanish (#91): after a reload the stage and the phone both speak it
        session = page.request.get(f"{base}/api/sessions/{sid}").json()["session"]
        assert page.request.put(f"{base}/api/sessions/{sid}", data={"session": {**session, "language": "es"}}).ok
        for _ in range(50):
            if page.request.get(f"{base}/api/live").json()["plan"]["run"].get("language") == "es":
                break
            page.wait_for_timeout(100)
        stage.reload()
        ana.reload()
        expect(stage.locator(".qz-board-title")).to_have_text("Clasificación")
        expect(ana.locator("[data-result-detail]")).to_contain_text("Puntuación")
        assert ana.evaluate("document.documentElement.lang") == "es"

        assert page.request.post(f"{base}/api/actions/goto/{podium}").ok
        shown = stage.locator(".qz-step.shown .qz-pname")  # laid out 2nd, 1st, 3rd
        expect(stage.locator(".qz-step")).to_have_count(3)
        expect(shown).to_have_count(0)  # nothing yet: each Next reveals one place
        for names in (["Cy"], ["Bo", "Cy"], ["Bo", "Ana", "Cy"]):  # 3rd, then 2nd, then 1st
            assert page.request.post(f"{base}/api/actions/next").ok
            expect(shown).to_have_text(names)
        strip = next(it for it in items if it["id"] == "pq-podium")["zone"]
        assert strip and stage.evaluate(IN_CAMERA, strip) == []
        assert page.request.post(f"{base}/api/actions/next").ok  # the fourth Next moves on
        assert page.request.get(f"{base}/api/live").json()["state"]["index"] == podium  # 0-based: the item after
        assert errors == []
    finally:
        ana.context.close()
        bo.context.close()
        stage.context.close()
        page.request.post(f"{base}/api/live/deactivate")
