"""Story 7: capture an activity — Space starts it, simulated answers grow the
word cloud on the stage (the rehearsal switch flips the "counting your
messages" chip on and off), a message is hidden, two phones in the room answer
the same cloud (one changes its answer, the stage shows chat and phones apart),
Space stops it and freezes it;
then the timer drives it: resuming reopens it, paused shows yellow, a reset
stops it; on a slide with a timer, Space starts the timer over the slide;
last, a private feedback form: two phones send three responses (one from the
room view, one through the plain link, one a second response), the backstage
adds them up, the CSV has a row each, the stage shows no answer, and a closed
form says so."""

from __future__ import annotations

import json
import time

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e._surface import surface
from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session
from tests.fixtures.forms import FORM_SECTION
from tests.fixtures.quiz_plan import add_quiz_section

# A crowded cloud drawn into a live body with the stage's own renderer: `entries` is the
# result's word list, `names` the show-names switch. Resolves once the words settled.
DRAW_CLOUD = """async ({ entries, names, bodySel }) => {
  const mod = await import('/activities/word_cloud/stage.js');
  const body = document.querySelector(bodySel);
  mod.render(body, { words: entries, answers: entries.length, people: entries.length }, { names, lang: 'en', options: {} });
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  await Promise.all(body.getAnimations({ subtree: true }).map((a) => a.finished.catch(() => null)));
  const box = body.getBoundingClientRect();
  const words = [...body.querySelectorAll('.wc-word')].map((el) => {
    const r = el.getBoundingClientRect();
    return { key: el.dataset.key, px: parseFloat(el.style.fontSize),
             inside: r.left >= box.left - 1 && r.top >= box.top - 1 && r.right <= box.right + 1 && r.bottom <= box.bottom + 1 };
  });
  const more = body.querySelector('.wc-more');
  return { words, more: more ? more.textContent : '' };
}"""

_FIRST = ["amber", "brisk", "calm", "dusk", "ember", "fable", "grove", "haze", "ivory", "jade", "kite", "lumen"]
_SECOND = ["anchor", "bridge", "candle", "delta", "echo", "forest"]


def synthetic_entries(n: int, twice: int = 0, words_per_entry: int = 2) -> list[dict]:
    """``n`` made-up entries (the first ``twice`` counted twice), each ``words_per_entry`` words long."""
    out = []
    for i in range(n):
        text = " ".join(f"{_FIRST[(i + j) % len(_FIRST)]}{'' if j else _SECOND[i % len(_SECOND)]}{i}" for j in range(words_per_entry))
        out.append({"key": f"k{i}", "text": text, "count": 2 if i < twice else 1, "names": ["Ana", "Ben"] if i % 3 == 0 else [], "first": i})
    return out


def draw_cloud(page: Page, entries: list[dict], names: bool, body: str = "[data-body]") -> dict:
    return page.evaluate(DRAW_CLOUD, {"entries": entries, "names": names, "bodySel": body})


def check_crowded_clouds(page: Page, stage: Page) -> None:
    # a crowded cloud: every entry is drawn inside the body, a two-answer entry larger than a one-answer one;
    # what cannot fit is counted ("+N more"), the least-answered entries first, and the presenter says how many
    for names in (False, True):
        crowd = draw_cloud(stage, synthetic_entries(33, twice=2), names)
        assert len(crowd["words"]) == 33 and crowd["more"] == ""
        assert all(w["inside"] for w in crowd["words"])
        sizes = {w["key"]: w["px"] for w in crowd["words"]}
        assert min(sizes["k0"], sizes["k1"]) > max(px for k, px in sizes.items() if k not in ("k0", "k1"))
    same = draw_cloud(stage, synthetic_entries(22), True)
    assert len(same["words"]) == 22 and all(w["inside"] for w in same["words"])
    assert len({w["px"] for w in same["words"]}) == 1
    verbatim = synthetic_entries(60, twice=3, words_per_entry=9)
    over = draw_cloud(stage, verbatim, False)
    left = 60 - len(over["words"])
    assert left > 0 and over["more"] == f"+{left} more"
    assert all(w["inside"] for w in over["words"])
    drawn = {w["key"] for w in over["words"]}
    assert {"k0", "k1", "k2"} <= drawn  # an entry said twice is never left out while a single one is drawn
    assert drawn == {e["key"] for e in verbatim[:len(drawn)]}  # the left-out ones are the last in the ranking
    preview = draw_cloud(page, verbatim, False, ".p-now .stage-canvas [data-body]")  # its own body, so its own count
    expect(page.locator("[data-capleft]")).to_have_text(f"{60 - len(preview['words'])} entries not shown on the stage")


def exclude_cohost(page: Page, stage: Page, base_url: str) -> None:
    """A co-host account is not a participant: click its name in the chat and nothing it writes counts."""
    cohost = {"batch": "cohost", "messages": [{"sender": "Co Host", "text": "ten minutes break", "time": "18:42"}]}
    assert page.request.post(f"{base_url}/api/chat/messages", data=cohost).ok
    expect(page.locator("[data-capcounts]")).to_contain_text("13 answers", timeout=20000)
    page.locator(".p-msg", has_text="ten minutes break").locator(".p-sender").click()
    expect(page.locator(".p-msg.excluded-msg")).to_have_count(1)
    expect(page.locator("[data-capcounts]")).to_contain_text("12 answers from")
    expect(page.locator("[data-capcounts]")).to_contain_text("1 not counted")
    expect(stage.locator(".wc-word", has_text="ten minutes break")).to_have_count(0)
    expect(page.locator("[data-excluded] [data-restore]")).to_have_count(1)


def phones_answer_the_cloud(page: Page, browser: Browser, stage: Page, webapp) -> None:
    """Two phones in the room answer the same word cloud as the chat: one named, one anonymous; the
    named one changes its answer, which replaces the old one; the stage shows chat and phones apart."""
    pin = page.request.get(f"{webapp.base_url}/api/live").json()["state"]["room"]["pin"]
    spec = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    phones = [browser.new_context(**spec).new_page(), browser.new_context(**spec).new_page()]  # two phones, two storages
    try:
        for phone, name in zip(phones, ("Ana", ""), strict=True):
            phone.goto(f"{webapp.player_url}/play?pin={pin}")
            expect(phone.locator("#nickname")).to_have_attribute("placeholder", "Your name (optional)")
            phone.fill("#nickname", name)
            phone.click("#joinBtn")
            expect(phone.locator("[data-poll-question]")).to_contain_text("kryptonite")
        ana, anon = phones
        surface(ana, "player.poll")
        for phone, text in ((ana, "deadlines"), (anon, "silence"), (ana, "noise")):
            phone.fill("#pollText", text)
            phone.click("[data-poll-send]")
            expect(phone.locator("[data-poll-note]")).to_contain_text(f"Your answer: {text}")
        expect(page.locator("[data-capcounts]")).to_contain_text("13 answers from")
        expect(page.locator("[data-capcounts]")).to_contain_text("2 by phone")
        expect(stage.locator(".wc-word", has_text="noise")).to_be_visible()
        expect(stage.locator(".wc-word", has_text="deadlines")).to_have_count(0)  # replaced by Ana's new answer
        expect(stage.locator("[data-count-chat]")).to_contain_text("11")
        expect(stage.locator("[data-count-room]")).to_contain_text("2")
    finally:
        for phone in phones:
            phone.context.close()


def _send_form(phone: Page, name: str, text: str, taps: list[str]) -> None:
    """Fill the form page: the text answer, the buttons in ``taps`` (``q3=8``, ``q6=A``), the name; Send."""
    phone.locator('[data-q="q1"] textarea').fill(text)
    for tap in taps:
        q, value = tap.split("=")
        phone.locator(f'[data-q="{q}"] [data-pick="{value}"]').click()
    phone.fill("#fmName", name)
    phone.click("#formSend")
    expect(phone.locator("[data-for=thanks]")).to_be_visible()


def phones_send_the_feedback_form(page: Page, browser: Browser, stage: Page, webapp, folder) -> None:
    """A private form: Ana opens it from the room view and sends twice, an anonymous phone opens the plain
    link once; the presenter's panel adds the three up, the CSV has a row each, the stage shows none."""
    run = page.request.get(f"{webapp.base_url}/api/live").json()
    form = next(it for it in run["plan"]["run"]["items"] if it["id"] == "act-form")
    page.request.post(f"{webapp.base_url}/api/actions/goto/{form['index'] + 1}")
    expect(page.locator(".p-item .p-label")).to_have_text("Feedback form")
    expect(page.locator("[data-fstate]")).to_have_text("Open — taking responses")  # opened by coming on stage
    expect(stage.locator("[data-form-slide]")).to_be_visible()
    link = page.request.get(f"{webapp.base_url}/api/forms/act-form/results").json()["link"]
    spec = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    ana, anon = browser.new_context(**spec).new_page(), browser.new_context(**spec).new_page()
    try:
        ana.goto(f"{webapp.player_url}/play?pin={run['state']['room']['pin']}")
        ana.click("#joinBtn")  # the room needs no name
        surface(ana, "player.form-link")
        ana.click("[data-form-link]")
        surface(ana, "player.form")
        _send_form(ana, "Ana", "the games", ["q3=8", "q4=5", "q5=10", "q6=A", "q6=B"])
        ana.click("[data-again]")
        _send_form(ana, "Ana", "more breaks", ["q5=9"])
        anon.goto(f"{webapp.player_url}{link}")  # the link pasted in the chat: no PIN, no join
        anon.click("#formSend")  # empty: the required questions say so, nothing is stored
        expect(anon.locator('[data-q="q5"] [data-q-error]')).to_be_visible()
        _send_form(anon, "", "the venue", ["q5=6", "q6=A"])
        expect(page.locator("[data-fcounts]")).to_contain_text("3 responses")
        expect(page.locator(".p-form-results")).to_contain_text("NPS +33")
        expect(page.locator(".p-form-texts li")).to_have_count(3)
        surface(page, "presenter.form")
        rows = (folder / "live" / "captures" / "act-form.csv").read_text(encoding="utf-8-sig").strip().splitlines()
        assert len(rows) == 4 and "Talks; Games" in rows[1]
        expect(stage.locator("[data-form-slide]")).not_to_contain_text("the games")
        page.locator("[data-ftoggle]").click()
        expect(page.locator("[data-fstate]")).to_contain_text("Closed")
        anon.reload()
        expect(anon.locator("[data-for=closed]")).to_contain_text("This form is closed")
    finally:
        ana.context.close()
        anon.context.close()


@pytest.mark.needs_chromium
def test_capture_a_word_cloud_and_freeze_it(page: Page, browser: Browser, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "capture-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    add_quiz_section(folder, FORM_SECTION, at=99)  # a feedback form, last
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

    # the rehearsal switch: off by default, a warning chip in the header while it's on
    own = page.locator("[data-count-own]")
    expect(own).to_have_attribute("aria-checked", "false")
    own.click()
    expect(own).to_have_attribute("aria-checked", "true")
    expect(page.locator(".p-chips .chip.warn", has_text="Counting your messages")).to_be_visible()
    mine = {"batch": "rehearsal", "messages": [{"sender": "You", "text": "rehearsal", "time": "18:41"}]}
    assert page.request.post(f"{webapp.base_url}/api/chat/messages", data=mine).ok
    expect(stage.locator(".wc-word", has_text="rehearsal")).to_be_visible()  # my own answer is on the stage
    own.click()
    expect(own).to_have_attribute("aria-checked", "false")
    expect(page.locator(".p-chips", has_text="Counting your messages")).to_have_count(0)
    expect(stage.locator(".wc-word", has_text="rehearsal")).to_have_count(0)  # off: the live rule again

    resp = page.request.post(f"{webapp.base_url}/api/chat/simulate",
                             data={"kind": "burst", "count": 12, "answers": ["meetings", "perfectionism", "hello"]})
    assert resp.ok
    expect(page.locator("[data-capcounts]")).to_contain_text("12 answers", timeout=20000)
    expect(stage.locator(".wc-word", has_text="perfectionism 2")).to_be_visible()
    expect(stage.locator(".wc-word", has_text="hello 3")).to_be_visible()
    expect(stage.locator(".st-count")).to_contain_text("12")

    exclude_cohost(page, stage, webapp.base_url)

    # "hello 3" is noise: click it in the presenter's chat to hide it
    page.locator(".p-msg", has_text="hello 3").click()
    expect(page.locator(".p-msg.hidden-msg")).to_have_count(1)
    expect(page.locator("[data-capcounts]")).to_contain_text("11 answers from")
    expect(page.locator("[data-capcounts]")).to_contain_text("1 hidden")
    expect(stage.locator(".wc-word", has_text="hello 3")).to_have_count(0)
    shot(page, shots / "story-07-capture-1-presenter.png")
    shot(stage, shots / "story-07-capture-2-stage.png")
    surface(page, "presenter.capture")

    check_crowded_clouds(page, stage)
    phones_answer_the_cloud(page, browser, stage, webapp)

    page.keyboard.press("Space")
    expect(page.locator("[data-captoggle]")).to_contain_text("Reopen capture")
    frozen = json.loads((folder / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))
    assert frozen["result"]["answers"] == 13
    assert frozen["result"]["sources"] == {"room": 2, "chat": 11}
    assert sum(a["hidden"] for a in frozen["answers"]) == 1
    png = folder / "live" / "captures" / "act-kryptonite.png"
    deadline = time.time() + 30
    while not png.is_file() and time.time() < deadline:
        time.sleep(0.5)
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # the stop paused the timer; resuming it reopens the capture (a running timer always captures)
    expect(page.locator("[data-tstate]")).to_have_text("paused")
    expect(page.locator("[data-tclock]")).to_have_class("p-timer-clock paused")
    expect(stage.locator("[data-pill]")).to_have_class("st-pill paused")
    page.keyboard.press("t")
    expect(page.locator("[data-captoggle]")).to_contain_text("Stop capture")
    expect(stage.locator("[data-pill]")).to_have_class("st-pill running")  # #211
    # a timer reset stops the capture instead of leaving it open
    page.locator("[data-treset]").click()
    expect(page.locator("[data-captoggle]")).to_contain_text("Reopen capture")
    expect(page.locator("[data-tstate]")).to_have_text("not started")

    # a slide with a timer: Space starts it (nothing to capture) and it shows over the slide
    items = page.request.get(f"{webapp.base_url}/api/live").json()["plan"]["run"]["items"]
    readme = next(it for it in items if it["title"] == "Personal readme instructions")
    page.request.post(f"{webapp.base_url}/api/actions/goto/{readme['index'] + 1}")
    expect(stage.locator(".st-slide")).to_be_visible()
    expect(stage.locator("[data-pill]")).to_be_hidden()
    page.keyboard.press("Space")
    expect(stage.locator(".st-slide-pill")).to_be_visible()
    expect(stage.locator(".st-slide-pill")).to_contain_text("04:")
    shot(stage, shots / "story-07-capture-3-slide-timer.png")
    phones_send_the_feedback_form(page, browser, stage, webapp, folder)
    stage_ctx.close()
