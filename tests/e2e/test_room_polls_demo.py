"""The Room Polls demo, end to end: the demo session opens in the editor and the presenter,
two phones join the room on the Join slide, play the quiz, answer every poll beside fake chat
lines (each result renders on the stage and freezes into a capture), and send the private
form, whose CSV has a row per response. Every step is screenshotted into a proof page.

Not part of the gate (the suite keeps under 15 nodes, ``conftest.py``'s ``collect_ignore``):
run it on purpose, by path, after a change to the room, the polls or the forms:

    .venv\\Scripts\\python.exe -m pytest tests/e2e/test_room_polls_demo.py

The proof page lands in ``data/proof/room-polls-demo/index.html`` (``FS_PROOF_DIR`` moves it).
"""

from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.fixtures.room_polls_demo import build_room_polls_demo, demo_items

REPO = Path(__file__).resolve().parents[2]
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}

# Per poll: what the two phones answer and the chat lines beside them (synthetic).
# A phone answer is ("text"|"number", value), ("tap", value) or ("multi", "A,C").
ANSWERS: dict[str, dict[str, Any]] = {
    "demo-cloud": {"phones": [("text", "curious"), ("text", "sleepy")], "chat": ["curious", "focused", "happy"]},
    "demo-choice": {"phones": [("tap", "B"), ("tap", "A")], "chat": ["B", "c", "Home"]},
    "demo-multi": {"phones": [("multi", "A,C"), ("multi", "B")], "chat": ["A, B", "coffee", "D"]},
    "demo-scale": {"phones": [("tap", "7"), ("tap", "4")], "chat": ["8", "6", "10"]},
    "demo-stars": {"phones": [("tap", "4"), ("tap", "5")], "chat": ["5", "****", "3"]},
    "demo-nps": {"phones": [("tap", "9"), ("tap", "6")], "chat": ["10", "8", "9"]},
    "demo-number": {"phones": [("number", "25"), ("number", "40")], "chat": ["15", "about 30", "5"]},
    "demo-text": {"phones": [("text", "A focus block every morning"), ("text", "Fewer meetings")],
                  "chat": ["Walk while I think", "Share the agenda first", "Say no kindly"]},
}
CHAT_PEOPLE = ["Alex R.", "Sam T.", "Jordan P."]


class Proof:
    """Screenshots in order and the page that shows them."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.png"):
            old.unlink()
        self.rows: list[tuple[str, list[str]]] = []

    def snap(self, caption: str, **pages: Page) -> None:
        files = []
        for who, page in pages.items():
            name = f"{len(self.rows) + 1:02d}-{who}.png"
            page.screenshot(path=str(self.folder / name), animations="disabled", caret="hide")
            files.append(name)
        self.rows.append((caption, files))

    def write(self, verdict: str) -> Path:
        body = "".join(
            f"<section><h2>{n}. {html.escape(caption)}</h2>"
            + "".join(f'<img src="{f}" alt="{html.escape(f)}" loading="lazy">' for f in files) + "</section>"
            for n, (caption, files) in enumerate(self.rows, start=1))
        page = self.folder / "index.html"
        page.write_text(
            "<!doctype html><meta charset=utf-8><title>Room Polls demo proof</title>"
            "<style>body{font:15px system-ui;margin:24px;background:#f6f6f6}section{margin:0 0 28px}"
            "img{max-height:420px;margin:6px 12px 0 0;border:1px solid #ccc;background:#fff;vertical-align:top}"
            "h2{font-size:16px;margin:0 0 4px}</style>"
            f"<h1>Room Polls demo · {html.escape(verdict)}</h1>"
            f"<p>{time.strftime('%Y-%m-%d %H:%M')} · disposable instance, synthetic data</p>{body}",
            encoding="utf-8")
        return page


def _goto(page: Page, base: str, item_id: str) -> dict[str, Any]:
    """Put the item on the stage; returns its run item."""
    items = page.request.get(f"{base}/api/live").json()["plan"]["run"]["items"]
    item = next(it for it in items if it["id"] == item_id)
    assert page.request.post(f"{base}/api/actions/goto/{item['index'] + 1}").ok
    return item


def _open_session(page: Page, base: str, sid: str, proof: Proof) -> None:
    """The demo opens in the editor (every section listed) and goes live in the presenter."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    page.goto(base + "/")
    page.click("#tabPlan")
    expect(page.locator(".sec-row")).to_have_count(4)
    expect(page.locator("#panePlan")).to_contain_text("Session feedback")
    proof.snap("The demo session in the Plan editor", editor=page)
    assert page.request.post(f"{base}/api/live/activate", data={"session": sid}).ok
    page.goto(f"{base}/presenter?session={sid}")
    expect(page.locator(".p-item .p-label")).to_be_visible()


def _join_room(page: Page, base: str, webapp, stage: Page, phones: list[Page], proof: Proof) -> None:
    """The Join slide is on the stage; one phone joins with a name, the other skips it."""
    _goto(page, base, "demo-join")
    expect(stage.locator("[data-join-slide]")).to_be_visible()  # no public URL here: it says to use the chat
    pin = page.request.get(f"{base}/api/live").json()["state"]["room"]["pin"]
    for phone, name in zip(phones, ("Ana", ""), strict=True):
        phone.goto(f"{webapp.player_url}/play?pin={pin}")
        phone.fill("#nickname", name)
        phone.click("#joinBtn")
        expect(phone.locator("[data-for=wait]")).to_contain_text("You're in")
        expect(phone.locator("[data-heading]")).to_have_text("Room")  # joined, nothing to answer yet
    assert page.request.get(f"{base}/api/live").json()["state"]["room"]["member_count"] == 2
    proof.snap("The Join slide; two phones joined (one named, one anonymous)", stage=stage, phone=phones[0])


def _play_quiz(page: Page, base: str, stage: Page, phones: list[Page], proof: Proof) -> None:
    """The quiz: each room member joins the game at the lobby (the anonymous one picks a nickname,
    the quiz needs one), both answer, the podium ranks them."""
    _goto(page, base, "demo-quiz-lobby")
    ana, anon = phones
    for phone, name in ((ana, "Ana"), (anon, "Bo")):
        expect(phone.locator("#nickname")).to_be_visible()
        phone.fill("#nickname", name)
        phone.click("#joinBtn")
        expect(phone.locator("[data-for=wait]")).to_contain_text("You're in")
    expect(stage.locator(".qz-player")).to_have_count(2)
    proof.snap("Quiz lobby: both players in", stage=stage, phone=anon)
    for right, wrong in (("2", "1"), ("1", "2")):  # each question: Ana right, Bo wrong
        assert page.request.post(f"{base}/api/actions/next").ok
        expect(ana.locator(".tile").first).to_be_visible()
        expect(ana.locator("[data-heading]")).to_have_text("Quiz")
        ana.locator(f'.tile[data-choice="{right}"]').click()
        anon.locator(f'.tile[data-choice="{wrong}"]').click()
        for phone in phones:
            expect(phone.locator("[data-answer-status]")).to_contain_text("Locked in")
        assert page.request.post(f"{base}/api/actions/next").ok  # reveal
        expect(stage.locator(".qz-tile.correct")).to_have_attribute("data-choice", right)
        expect(ana.locator("[data-result]")).to_contain_text("Correct")
        proof.snap(f"Quiz reveal (correct answer {right})", stage=stage, phone=ana)
        if right == "2":
            assert page.request.post(f"{base}/api/actions/next").ok  # leaderboard
            expect(stage.locator(".qz-row .qz-who")).to_have_text(["Ana", "Bo"])
    for _ in range(3):  # the podium, one place per Next
        assert page.request.post(f"{base}/api/actions/next").ok
    expect(stage.locator(".qz-step.shown .qz-pname")).to_have_count(2)
    expect(ana.locator("[data-result]")).to_have_text("#1")
    proof.snap("Quiz podium", stage=stage, phone=ana)


def _answer_on_phone(phone: Page, how: str, value: str) -> None:
    if how == "text":
        phone.fill("#pollText", value)
        phone.click("[data-poll-send]")
    elif how == "number":
        phone.fill("#pollNumber", value)
        phone.click("[data-poll-number-send]")
    elif how == "multi":
        for key in value.split(","):
            phone.locator(f'.play-choice-btn[data-key="{key}"]').click()
        phone.click("[data-poll-choice-send]")
    else:
        phone.locator(f'.play-scale-btn[data-value="{value}"], .play-choice-btn[data-key="{value}"]').click()
    expect(phone.locator("[data-poll-note]")).to_contain_text("Your answer")


def _run_poll(page: Page, base: str, stage: Page, phones: list[Page], folder: Path, item: dict[str, Any],
              proof: Proof) -> None:
    """One poll: the capture runs, both phones and three chat people answer, the stage counts both
    channels, the stop freezes it into a capture JSON and PNG."""
    plan = ANSWERS[item["id"]]
    _goto(page, base, item["id"])
    assert page.request.post(f"{base}/api/actions/capture_toggle").ok
    expect(page.locator("[data-captoggle]")).to_contain_text("Stop capture")
    for phone, (how, value) in zip(phones, plan["phones"], strict=True):
        expect(phone.locator("[data-poll-question]")).to_contain_text(item["question"][:20])
        expect(phone.locator("[data-heading]")).to_have_text("Poll")
        _answer_on_phone(phone, how, value)
    chat = {"batch": item["id"], "messages": [{"sender": who, "text": text, "time": "10:0" + str(n)}
                                              for n, (who, text) in enumerate(zip(CHAT_PEOPLE, plan["chat"], strict=True))]}
    assert page.request.post(f"{base}/api/chat/messages", data=chat).ok
    expect(stage.locator("[data-count-room]")).to_contain_text("2", timeout=20000)
    expect(stage.locator("[data-count-chat]")).to_contain_text("3", timeout=20000)
    proof.snap(f"{item['type_label']}: {item['question']}", stage=stage, phone=phones[0])
    assert page.request.post(f"{base}/api/actions/capture_toggle").ok
    expect(page.locator("[data-captoggle]")).to_contain_text("Reopen capture")
    frozen = json.loads((folder / "live" / "captures" / f"{item['id']}.json").read_text(encoding="utf-8"))
    assert frozen["result"]["sources"] == {"room": 2, "chat": 3}, (item["id"], frozen["result"].get("sources"))
    png = folder / "live" / "captures" / f"{item['id']}.png"
    deadline = time.time() + 30
    while not png.is_file() and time.time() < deadline:
        time.sleep(0.5)
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", item["id"]


def _send_form(phone: Page, name: str, picks: list[str], text: str) -> None:
    for pick in picks:
        q, value = pick.split("=")
        phone.locator(f'[data-q="{q}"] [data-pick="{value}"]').click()
    phone.locator('[data-q="q5"] textarea').fill(text)
    phone.fill("#fmName", name)
    phone.click("#formSend")
    expect(phone.locator("[data-for=thanks]")).to_be_visible()


def _send_the_form(page: Page, base: str, webapp, stage: Page, phones: list[Page], folder: Path, proof: Proof) -> None:
    """The private form: one phone from the room view, one through the plain link; the backstage adds
    them up, the CSV has a row each, the stage shows no answer."""
    _goto(page, base, "demo-form")
    expect(page.locator("[data-fstate]")).to_have_text("Open — taking responses")
    expect(stage.locator("[data-form-slide]")).to_be_visible()
    ana, anon = phones
    expect(ana.locator("[data-heading]")).to_have_text("Feedback")  # the room view shows the form link
    ana.click("[data-form-link]")
    _send_form(ana, "Ana", ["q1=9", "q2=4", "q3=A", "q3=B", "q4=4"], "More time for the polls")
    proof.snap("The private form, sent from a phone", phone=ana)
    link = page.request.get(f"{base}/api/forms/demo-form/results").json()["link"]
    anon.goto(f"{webapp.player_url}{link}")  # the link pasted in the chat: no PIN, no join
    _send_form(anon, "", ["q1=7", "q3=C"], "Shorter quiz")
    expect(page.locator("[data-fcounts]")).to_contain_text("2 responses")
    expect(stage.locator("[data-form-slide]")).not_to_contain_text("Shorter quiz")
    proof.snap("Form results backstage; the stage shows no answers", presenter=page, stage=stage)
    rows = (folder / "live" / "captures" / "demo-form.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(rows) == 3, rows
    csv = page.request.get(f"{base}/api/forms/demo-form/csv")
    assert csv.ok and "More time for the polls" in csv.text()


@pytest.mark.needs_chromium
def test_the_room_polls_demo_end_to_end(page: Page, browser: Browser, webapp) -> None:
    base = webapp.base_url
    folder = webapp.root / "sessions" / "demo" / "room-polls-demo"
    sid, _ = build_room_polls_demo(folder, webapp.root / "sessions.local.yaml")
    proof = Proof(Path(os.environ.get("FS_PROOF_DIR") or REPO / "data" / "proof" / "room-polls-demo"))
    stage = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
    phones = [browser.new_context(**PHONE).new_page(), browser.new_context(**PHONE).new_page()]  # two phones, two storages
    verdict = "FAILED"
    try:
        _open_session(page, base, sid, proof)
        stage.goto(f"{base}/stage")
        _join_room(page, base, webapp, stage, phones, proof)
        _play_quiz(page, base, stage, phones, proof)
        items = page.request.get(f"{base}/api/live").json()["plan"]["run"]["items"]
        for item in items:
            if item["id"] in ANSWERS:
                _run_poll(page, base, stage, phones, folder, item, proof)
        assert {it["id"] for it in demo_items()} >= set(ANSWERS)
        _send_the_form(page, base, webapp, stage, phones, folder, proof)
        verdict = "PASSED"
    finally:
        print(f"PROOF={proof.write(verdict)}")
        for p in (stage, *phones):
            p.context.close()
