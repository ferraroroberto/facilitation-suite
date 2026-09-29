"""Story 6: rehearse with simulated chat — a 50-message burst goes through the
reader process and the server and lands on the presenter, none lost. Then a
quiz (#54): the presenter's Quiz card, chat answers scored as quiz answers,
Space locking the question, removing a player, and the phone remote. On the first slide
with the chat full, and with the quiz question open, the cockpit fits a 1920x1080 second
monitor and the 1920x911 the stage chip reports (#149): no page scroll, the Zoom chat in
view with at least five messages, and "Then" listing up to four items with no scrollbar."""

from __future__ import annotations

import json
import re
import uuid

from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session, demo_person
from tests.fixtures.quiz_run import add_quiz_section

# The presenter at a wide viewport (#149): how far the page scrolls, how many chat messages
# are whole in view, how many items "Then" lists and how far the Next card's body overflows.
COCKPIT_FIT = """() => {
  const doc = document.documentElement, next = document.querySelector('.p-next [data-body]');
  const box = document.querySelector('.p-msgs').getBoundingClientRect();
  const bottom = Math.min(box.bottom, innerHeight);
  const inView = [...document.querySelectorAll('.p-msg')].filter((m) => {
    const r = m.getBoundingClientRect();
    return r.top >= box.top - 1 && r.bottom <= bottom + 1;
  }).length;
  return { page: doc.scrollHeight - doc.clientHeight, chat: inView,
    then: document.querySelectorAll('.p-then li').length, overflow: next.scrollHeight - next.clientHeight };
}"""


def _cockpit_fits(page: Page, then: int) -> None:
    """At 1920x1080 and 1920x911: no page scroll, five chat messages or more in view, and "Then"
    listing ``then`` items without overflowing the Next card (#149); back to 1440x900 after."""
    for height in (1080, 911):
        page.set_viewport_size({"width": 1920, "height": height})
        fit = page.evaluate(COCKPIT_FIT)
        assert fit["page"] <= 0 and fit["chat"] >= 5 and fit["then"] == then and fit["overflow"] <= 1, (height, fit)
    page.set_viewport_size({"width": 1440, "height": 900})


def test_a_simulated_burst_reaches_the_presenter(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "chat-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{webapp.base_url}/presenter?session={sid}")
    expect(page.locator(".p-chips")).to_contain_text("Zoom chat · off")

    resp = page.request.post(f"{webapp.base_url}/api/chat/simulate", data={"kind": "burst", "count": 50})
    assert resp.ok
    expect(page.locator(".p-chat .p-meta")).to_have_text("50 messages", timeout=20000)
    expect(page.locator(".p-msg")).to_have_count(50)
    expect(page.locator(".p-chips")).to_contain_text("Zoom chat · off", timeout=15000)  # the simulation ended

    lines = (folder / "live" / "chat.jsonl").read_text(encoding="utf-8").splitlines()
    texts = [json.loads(line)["text"] for line in lines]
    assert len(texts) == 50 and texts[0].endswith(" 1") and texts[-1].endswith(" 50")
    page.locator("[data-keys]").click()
    expect(page.locator("[data-keypop]")).to_be_visible()
    page.locator("[data-keys]").click()
    shot(page, shots / "story-06-chat-1-presenter.png")
    _cockpit_fits(page, then=4)  # #149: the first slide, 50 messages in the chat

    # ---- #54: a quiz, answered from the Zoom chat ----
    base = webapp.base_url
    qfolder = webapp.root / "sessions" / "demo" / "quiz-chat-story"
    qsid, _ = build_demo_session(qfolder, webapp.root / "sessions.local.yaml")
    add_quiz_section(qfolder)  # lobby, four questions, podium: the plan's last six items
    page.goto(f"{base}/presenter?session={qsid}")
    expect(page.locator(".p-sub")).to_contain_text("1 of 23")
    assert page.request.post(f"{base}/api/actions/goto/18").ok  # the quiz lobby
    card = page.locator(".p-item")
    expect(card.locator(".p-label")).to_have_text("Quiz")
    expect(card.locator("[data-qphase]")).to_have_text("Lobby — players are joining")
    expect(card.locator("[data-qjoin]")).to_contain_text("not configured")  # no public link in a disposable instance
    expect(card.locator("[data-qnew]")).to_be_visible()
    expect(card.locator("[data-qlock]")).to_be_disabled()

    card.locator("[data-qnext]").click()  # "Start the first question" = next
    expect(card.locator("[data-qphase]")).to_have_text("Question 1 of 4 — answers open")
    expect(card.locator("[data-qleft]")).to_have_text(re.compile(r"^00:(1\d|20)$"))
    rows = [{"sender": demo_person(1), "text": "b."}, {"sender": demo_person(2), "text": "C"},
            {"sender": demo_person(3), "text": "hello"}, {"sender": "You", "text": "A"},
            {"sender": demo_person(1), "text": "A"}]  # a second answer changes nothing
    assert page.request.post(f"{base}/api/chat/messages", data={"batch": uuid.uuid4().hex, "source": "zoom", "messages": rows}).ok
    expect(card.locator("[data-qcounts]")).to_contain_text("2 players joined · 2 answered")
    expect(card.locator(".p-quiz-players li .chip")).to_have_count(2)  # both marked "chat"
    shot(page, shots / "story-06-chat-2-quiz.png")
    _cockpit_fits(page, then=3)  # #149: a quiz question live (the plan's last three items under "Then")

    page.keyboard.press(" ")  # Space locks the open question
    expect(card.locator("[data-qphase]")).to_have_text("Question 1 of 4 — answers locked")
    quiz = page.request.get(f"{base}/api/live").json()["state"]["quiz"]
    assert quiz["distribution"] == [0, 1, 1, 0] and quiz["correct"] == [2]

    card.locator(f'[data-kick][data-name="{demo_person(2)}"]').click()
    page.locator(".dialog-confirm").click()
    expect(card.locator(".p-quiz-players li")).to_have_count(1)

    remote = browser.new_page(viewport={"width": 390, "height": 844})
    remote.goto(f"{base}/remote")
    expect(remote.locator("[data-r-qphase]")).to_have_text("Answers locked · question 1 of 4")
    expect(remote.locator("[data-r-qcounts]")).to_contain_text("1 player · 1 answered")
    expect(remote.locator("[data-r-qlock]")).to_be_disabled()
    remote.locator("[data-r-nextbtn]").click()  # the remote's Next steps the phases too
    expect(card.locator("[data-qphase]")).to_have_text("Leaderboard after question 1 of 4")
    remote.close()
