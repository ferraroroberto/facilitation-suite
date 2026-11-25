"""Mentimeter placeholder slides import as poll activities of the closest type."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.importer.mentimeter import UNREADABLE, classify_mentimeter
from src.importer.service import build_slides_meta, first_plan
from tests.fixtures import mentimeter_deck


def _pngs(folder: Path, export: dict[str, Any]) -> None:
    for s in export["slides"]:
        Image.new("RGB", (1920, 1080), (242, 242, 242)).save(folder / s["file"])


def test_the_fixture_deck_maps_word_cloud_choice_and_scale_slides(tmp_path: Path) -> None:
    export = mentimeter_deck.export()
    _pngs(tmp_path, export)
    meta = build_slides_meta(export, tmp_path, Path("deck.pptx"))
    items = [it for sec in first_plan(meta, 60) for it in sec.items]
    polls = [it for it in items if it.kind == "activity"]
    assert [(it.type, it.question) for it in polls] == [
        ("word_cloud", "One word for how you arrive today"),
        ("choice", "Where do you work most days?"),
        ("scale", "How much energy do you have, 1-10?"),
    ]
    assert polls[1].options == {"choices": "Office, Home, Both / it depends"}
    assert polls[2].options == {"min": 1, "max": 10}
    unreadable = next(it for it in items if it.slide_id == 24)
    assert (unreadable.kind, unreadable.include) == ("slide", False)
    assert meta["slides"][4]["placeholder"] == {"kind": "skip", "reason": UNREADABLE}


@pytest.mark.parametrize(("title", "texts", "kind", "options"), [
    ("Mentimeter – Scale – How ready are you?", [], "scale", {}),
    ("Mentimeter - how likely are you to recommend this workshop?", [], "nps", {}),
    ("Mentimeter 3 – rating – the venue", [], "stars", {}),
    ("Mentimeter – open ended – what would you change?", [], "cards", {}),
    ("Mentimeter – Numbers matter? Guess the team size", [], "word_cloud", {}),
    ("Mentimeter – pick one", ["Mentimeter – pick one", "• Red\n• Blue"], "choice", {"choices": "Red, Blue"}),
])
def test_the_type_comes_from_its_name_or_the_closest_guess(title: str, texts: list[str], kind: str,
                                                            options: dict[str, Any]) -> None:
    got = classify_mentimeter(title, texts)
    assert got is not None and (got["kind"], got["type"], got["options"]) == ("activity", kind, options)


@pytest.mark.parametrize("title", ["Mentimeter", "Mentimeter slide", "mentimeter 2", "Mentimeter – word cloud"])
def test_a_slide_with_no_question_stays_skipped_with_the_reason(title: str) -> None:
    assert classify_mentimeter(title, [title]) == {"kind": "skip", "reason": UNREADABLE}


def test_other_slides_are_not_mentimeter() -> None:
    assert classify_mentimeter("Working agreement", ["Working agreement"]) is None


def test_the_import_job_saves_the_polls_and_names_the_unreadable_slide(client, tmp_path: Path) -> None:
    def exporter(deck: Path, out: Path, progress, only=None) -> dict[str, Any]:
        export = mentimeter_deck.export()
        _pngs(out, export)
        return export

    sid = client.post("/api/sessions", json={"title": "Demo"}).json()["id"]
    client.app.state.importer._exporter = exporter
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"x")
    job_id = client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"]
    for _ in range(100):
        job = client.get(f"/api/imports/{job_id}").json()
        if not job["active"]:
            break
        time.sleep(0.05)
    assert job["state"] == "done", job
    assert job["result"]["unreadable"] == [{"index": 5, "title": "Mentimeter slide", "reason": UNREADABLE}]
    session = client.get(f"/api/sessions/{sid}").json()["session"]
    polls = [it for sec in session["sections"] for it in sec["items"] if it["kind"] == "activity"]
    assert [p["type"] for p in polls] == ["word_cloud", "choice", "scale"]
    assert polls[1]["options"]["choices"] == "Office, Home, Both / it depends"
