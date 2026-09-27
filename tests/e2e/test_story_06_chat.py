"""Story 6: rehearse with simulated chat — a 50-message burst goes through the
reader process and the server and lands on the presenter, none lost."""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session


def test_a_simulated_burst_reaches_the_presenter(page: Page, webapp, shots) -> None:
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
