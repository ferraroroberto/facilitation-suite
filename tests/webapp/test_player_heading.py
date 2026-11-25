"""The phone's header (``player/static/heading.js``): it names the activity on stage, not always "Quiz".

The module has no imports, so Node runs it as it is. The words themselves are in ``play.js``'s TEXT
table; ``tests/quiz/test_quiz_language.py`` checks both languages carry every key."""

from __future__ import annotations

import json
import re

import pytest

from src.config import PROJECT_ROOT
from tests.webapp.node_probe import probe_module

STATIC = PROJECT_ROOT / "app" / "webapp" / "player" / "static"
MODULE = STATIC / "heading.js"

VIEWS = {
    "quiz_question": {"phase": "question", "item_id": "q1", "tiles": [1, 2], "open": True},
    "quiz_lobby": {"phase": "lobby"},
    "quiz_podium": {"phase": "podium"},
    "poll_text": {"kind": "poll", "type": "word_cloud", "control": "text"},
    "poll_choice": {"kind": "poll", "type": "choice", "control": "choice"},
    "poll_stars": {"kind": "poll", "type": "stars", "control": "stars"},
    "form": {"kind": "form", "title": "Feedback", "link": "/play/form/x"},
    "room_waiting": {"kind": "room", "quiz_join": False},
    "room_quiz_join": {"kind": "room", "quiz_join": True},
    "none": None,
}

SCRIPT = """
const m = await import(process.argv[1]);
const views = __VIEWS__;
const out = { kinds: [...m.HEADINGS], heading: {} };
for (const [name, view] of Object.entries(views)) out.heading[name] = m.headingKind(view);
console.log(JSON.stringify(out));
""".replace("__VIEWS__", json.dumps(VIEWS))


@pytest.fixture(scope="module")
def probed() -> dict:
    return probe_module(MODULE, SCRIPT)


def test_the_header_names_the_activity_on_stage(probed: dict) -> None:
    assert probed["heading"] == {
        "quiz_question": "quiz", "quiz_lobby": "quiz", "quiz_podium": "quiz",
        "poll_text": "poll", "poll_choice": "poll", "poll_stars": "poll",
        "form": "form",
        "room_waiting": "room", "room_quiz_join": "quiz",
        "none": "idle",
    }


def test_every_heading_has_its_words_in_both_languages(probed: dict) -> None:
    text = (STATIC / "play.js").read_text(encoding="utf-8")
    for lang in ("en", "es"):
        table = re.search(rf"\n  {lang}: \{{\n(.*?)\n  \}},?\n", text, re.S).group(1)
        for kind in probed["kinds"]:
            assert re.search(rf"^    head_{kind}: '[^']+',$", table, re.M), (lang, kind)
