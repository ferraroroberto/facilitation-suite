"""Story 13: re-import review — the deck changed after the first import; the
Plan tab says a review is waiting, the review shows every change with before
and after, one change is declined, the rest apply and the plan follows."""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect

from tests.e2e.conftest import shot
from tests.fixtures.demo import build_demo_session, stage_demo_reimport


def test_review_a_reimport(page: Page, webapp, shots) -> None:
    folder = webapp.root / "sessions" / "demo" / "reimport-story"
    sid, _ = build_demo_session(folder, webapp.root / "sessions.local.yaml")
    stage_demo_reimport(folder)  # what the import job leaves after exporting the edited deck

    page.set_viewport_size({"width": 1280, "height": 900})
    page.add_init_script(f"localStorage.setItem('facilitation-suite.session', '{sid}')")
    page.goto(webapp.base_url + "/")
    page.click("#tabPlan")
    banner = page.locator(".review-banner")
    expect(banner).to_contain_text("waiting for your review")
    banner.locator("[data-review]").click()

    review = page.locator(".review")
    expect(review.locator(".review-head")).to_contain_text("demo-v2.pptx")
    for kind, n in (("identical", 5), ("modified", 3), ("new", 1), ("removed", 1), ("moved", 1)):
        expect(review.locator(f".review-tile.{kind} b")).to_have_text(str(n))
    expect(review.locator(".review-row")).to_have_count(6)
    removed = review.locator(".review-row[data-change='rem-109']")
    expect(removed).to_contain_text("The activity after it moves to follow slide 8")
    expect(review.locator(".review-row[data-change='new-111']")).to_contain_text("After 6 · How do we want to be remembered?")
    expect(review.locator(".review-row[data-change='mod-108']")).to_contain_text("Title changed from “The common enemy”")
    for img in review.locator(".review-row img.review-thumb").all():
        page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=img.element_handle())
    expect(review.locator("[data-apply]")).to_have_text("Apply 6 changes")
    shot(page, shots / "story-13-reimport-1-review.png")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(review.locator(".review-row").first).to_be_visible()
    shot(page, shots / "story-13-reimport-2-phone.png")
    page.set_viewport_size({"width": 1280, "height": 900})

    # keep the moved slide where it is in the plan
    review.locator(".review-row[data-change='mov-102'] [role=switch]").click()
    expect(review.locator(".review-row[data-change='mov-102']")).to_have_class("review-row declined")
    expect(review.locator("[data-apply]")).to_have_text("Apply 5 changes")
    review.locator("[data-apply]").click()
    expect(page.locator("#toast")).to_contain_text("Re-import applied")
    expect(page.locator("#toast")).to_contain_text("1 left as they were")
    expect(page.locator(".review-banner")).to_have_count(0)
    expect(page.locator(".plan-list")).to_contain_text("How do we want to be remembered?")
    expect(page.locator(".plan-list")).to_contain_text("Our common enemy")
    expect(page.locator(".plan-list .item-row", has_text="What do we need to win?")).to_have_count(1)  # only the feed; its slide is gone

    plan = page.request.get(f"{webapp.base_url}/api/sessions/{sid}").json()["session"]
    order = [it.get("slide_id") or it.get("id") for sec in plan["sections"] for it in sec["items"]]
    assert order.index(111) == order.index(106) + 1  # the new slide follows its deck neighbour
    assert order.index("act-ideas") == order.index("act-enemy") + 1  # the feed now follows slide 108's block
    assert order.index(102) < order.index(103)  # declined: the plan order is unchanged
    slides = json.loads((folder / "slides" / "slides.json").read_text(encoding="utf-8"))
    assert 109 not in [s["slide_id"] for s in slides["slides"]] and not (folder / "slides" / "incoming").exists()

    # the same deck again: the plan already has it (a declined move is not offered twice) — then cancel
    stage_demo_reimport(folder)
    page.reload()
    page.click("#tabPlan")
    page.locator(".review-banner [data-review]").click()
    expect(page.locator(".review .empty-state")).to_contain_text("Nothing changed in the deck")
    expect(page.locator(".review-tile.identical b")).to_have_text("10")
    page.locator("[data-cancel]").click()
    page.locator("dialog [data-ok]").click()
    expect(page.locator("#toast")).to_contain_text("Re-import cancelled")
    expect(page.locator(".plan-list")).to_be_visible()
    assert not (folder / "slides" / "incoming").exists()
