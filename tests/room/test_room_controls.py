"""Phone controls: what a phone in the room is shown for each poll type, and how the answer it
sends becomes the message text the type's parser reads (or a 422)."""

from __future__ import annotations

from typing import Any

import pytest

from src.activities.registry import parser
from src.room.controls import PollError, clean_value, control_of, fields


def item(kind: str, **options: Any) -> dict[str, Any]:
    return {"kind": "activity", "id": f"act-{kind}", "type": kind, "capture": True, "options": options}


@pytest.mark.parametrize(("kind", "control"), [
    ("word_cloud", "text"), ("cards", "text"), ("feed", "text"), ("scale", "scale"), ("nps", "scale"),
    ("stars", "stars"), ("choice", "choice"), ("number", "number"), ("map", None), ("quiz", None),
])
def test_each_type_has_its_phone_control(kind: str, control: Any) -> None:
    assert control_of(item(kind)) == control


def test_the_fields_each_control_draws() -> None:
    assert fields(item("word_cloud")) == {"max_length": 80}
    assert fields(item("cards")) == {"max_length": 280}
    assert fields(item("nps")) == {"min": 0, "max": 10, "labels": []}
    assert fields(item("stars")) == {"min": 1, "max": 5, "labels": []}
    assert fields(item("choice", choices="Yes, No", multiple=True)) == {
        "choices": [{"key": "A", "label": "Yes"}, {"key": "B", "label": "No"}], "multiple": True}
    assert fields(item("number", min=0, unit="hours")) == {"min": 0.0, "max": None, "unit": "hours"}


@pytest.mark.parametrize(("it", "value", "text"), [
    (item("nps"), "0", "0"), (item("nps"), 10, "10"), (item("stars"), "4", "4"),
    (item("choice", choices="Yes, No, Maybe"), "b", "B"),
    (item("choice", choices="Yes, No, Maybe", multiple=True), "C,A", "A,C"),
    (item("choice", choices="Yes, No, Maybe", multiple=True), ["B", "B"], "B"),
    (item("number"), "3,5", "3.5"), (item("number", min=0, max=100), " 42 ", "42"),
    (item("feed"), "  a  longer\nidea ", "a longer idea"),
])
def test_a_phone_answer_becomes_the_text_its_parser_reads(it: dict[str, Any], value: Any, text: str) -> None:
    assert clean_value(it, value) == text
    mod = parser(it["type"])
    assert mod.parse({"text": text, "sender": "Ana"}, {**it["options"]}) is not None


@pytest.mark.parametrize(("it", "value"), [
    (item("nps"), "11"), (item("stars"), "0"), (item("stars"), "four"),
    (item("choice", choices="Yes, No"), "C"), (item("choice", choices="Yes, No"), "A,B"), (item("choice", choices="Yes, No"), ""),
    (item("number", max=10), "11"), (item("number"), "lots"), (item("number"), "inf"), (item("cards"), "   "),
])
def test_an_answer_its_control_cannot_send_is_refused(it: dict[str, Any], value: Any) -> None:
    with pytest.raises(PollError) as err:
        clean_value(it, value)
    assert err.value.status == 422


def test_a_card_keeps_a_sentence_and_a_word_cloud_entry_stays_short() -> None:
    long = "word " * 100
    assert 80 < len(clean_value(item("cards"), long)) <= 280
    assert len(clean_value(item("word_cloud"), long)) <= 80
