"""The poll types' parsers: choice, stars, NPS and number read chat lines and phone answers alike
(a phone answer reaches the parser as the message text ``src/room/controls.py`` builds), one vote
per person, and each result's shape and report."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.activities.registry import editors, parser, preview, report_for, result_for, value_for
from src.room.model import ROOM_SOURCE

CHOICES = {"choices": "Office, Remote, Hybrid work, Not sure"}


def msgs(*texts: str, senders: Optional[list[str]] = None, room: bool = False) -> list[dict[str, Any]]:
    who = senders or [f"P{i}" for i in range(len(texts))]
    extra = {"source": ROOM_SOURCE} if room else {}
    return [{"id": i + 1, "sender": s, "text": t, "time": "18:41", "received_at": 0, **extra}
            for i, (s, t) in enumerate(zip(who, texts, strict=True))]


def parsed(kind: str, text: str, options: Optional[dict[str, Any]] = None) -> Any:
    mod = parser(kind)
    assert mod is not None
    c = mod.parse({"text": text, "sender": "Ana"}, {**{o["key"]: o.get("default") for o in editors()[kind]["options"]}, **(options or {})})
    return None if c is None else c["value"]


# ---------------------------------------------------------------------- choice


@pytest.mark.parametrize(("text", "keys"), [
    ("A", ["A"]), ("b", ["B"]), ("2", ["B"]), (" c. ", ["C"]),
    ("B) because it depends", ["B"]), ("remote", ["B"]), ("Hybrid work!", ["C"]),
    ("i'd say not sure", ["D"]), ("E", None), ("5", None), ("a lot of meetings", None), ("", None),
])
def test_choice_reads_a_letter_a_number_or_a_choices_words(text: str, keys: Optional[list[str]]) -> None:
    assert parsed("choice", text, CHOICES) == keys


@pytest.mark.parametrize(("text", "keys"), [
    ("B,D", ["B", "D"]), ("b, d", ["B", "D"]), ("1 y 3", ["A", "C"]), ("A and C", ["A", "C"]),
    ("d b b", ["D", "B"]), ("office and remote", ["A", "B"]),
])
def test_multiple_choice_reads_every_choice_named(text: str, keys: list[str]) -> None:
    assert parsed("choice", text, {**CHOICES, "multiple": True}) == keys
    assert parsed("choice", text, CHOICES) == keys[:1]  # one choice allowed: the first counts


def test_choice_counts_one_vote_per_person_with_shares() -> None:
    r = result_for("choice", CHOICES, msgs("A", "B", "B", "C", senders=["Ana", "Ana", "Sam", "Jo"]))
    assert [(b["key"], b["label"], b["count"], b["percent"]) for b in r["bars"]] == [
        ("A", "Office", 0, 0), ("B", "Remote", 2, 67), ("C", "Hybrid work", 1, 33), ("D", "Not sure", 0, 0)]
    assert r["bars"][1]["names"] == ["Ana", "Sam"]
    assert (r["votes"], r["answers"], r["people"]) == (3, 4, 3)
    rep = report_for("choice", r)
    assert rep["summary"] == "3 votes · most: B Remote" and rep["tile"] == "B"
    assert value_for("choice", {"value": ["B", "D"]}) == "B, D"


def test_multiple_choice_shares_count_each_voter_once_per_choice() -> None:
    r = result_for("choice", {**CHOICES, "multiple": True}, msgs("A,B", "B", "B,C,D"))
    assert {b["key"]: b["count"] for b in r["bars"]} == {"A": 1, "B": 3, "C": 1, "D": 1}
    assert r["bars"][1]["percent"] == 100 and r["multiple"] is True


def test_choice_takes_an_importers_list_too() -> None:
    assert parsed("choice", "beta with a comma", {"choices": ["Alpha", "Beta, with a comma"]}) == ["B"]


# ----------------------------------------------------------------------- stars


@pytest.mark.parametrize(("text", "stars"), [
    ("4", 4), ("4/5", 4), ("5 stars", 5), ("****", 4), ("★★★", 3), ("* * *", 3),
    ("0", None), ("6", None), ("******", None), ("great", None),
])
def test_stars_read_a_number_or_typed_stars(text: str, stars: Optional[int]) -> None:
    assert parsed("stars", text) == stars


def test_stars_average_and_spread() -> None:
    r = result_for("stars", {}, msgs("5", "3", "4", "4", senders=["Ana", "Sam", "Jo", "Ana"]))
    assert [(b["value"], b["count"]) for b in r["bins"]] == [(5, 0), (4, 2), (3, 1), (2, 0), (1, 0)]
    assert (r["average"], r["spread"], r["votes"]) == (3.67, 0.47, 3)
    assert report_for("stars", r)["summary"] == "3 votes · average 3.7 ± 0.5"
    empty = result_for("stars", {}, [])
    assert (empty["average"], empty["spread"], report_for("stars", empty)["tile"]) == (None, None, "– ★")


# ------------------------------------------------------------------------- NPS


@pytest.mark.parametrize(("text", "n"), [("9", 9), ("0", 0), ("10!", 10), ("maybe 7", 7), ("11", None), ("-1", None), ("no", None)])
def test_nps_reads_the_first_whole_number_from_0_to_10(text: str, n: Optional[int]) -> None:
    assert parsed("nps", text) == n


def test_nps_score_and_groups() -> None:
    r = result_for("nps", {}, msgs("10", "9", "8", "6", "3", "9", senders=["A", "B", "C", "D", "E", "B"]))
    assert (r["promoters"]["count"], r["passives"]["count"], r["detractors"]["count"]) == (2, 1, 2)
    assert r["score"] == 0 and r["votes"] == 5
    assert r["promoters"]["names"] == ["A", "B"] and r["detractors"]["percent"] == 40
    all_promoters = result_for("nps", {}, msgs("9", "10"))
    assert all_promoters["score"] == 100 and report_for("nps", all_promoters)["tile"] == "+100"
    assert result_for("nps", {}, msgs("0"))["score"] == -100
    assert result_for("nps", {}, [])["score"] is None


# ---------------------------------------------------------------------- number


@pytest.mark.parametrize(("text", "options", "n"), [
    ("42", {}, 42), ("about 3,5 hours", {}, 3.5), ("-2", {}, -2), ("none", {}, None),
    ("150 or 20", {"max": 100}, 20), ("5", {"min": 10}, None), ("12", {"min": "", "max": ""}, 12),
])
def test_number_reads_the_first_number_in_range(text: str, options: dict[str, Any], n: Optional[float]) -> None:
    assert parsed("number", text, options) == n


def test_number_histogram_per_whole_number_and_median() -> None:
    r = result_for("number", {"unit": "years"}, msgs("3", "5", "5", "4", "9", senders=["A", "B", "C", "A", "D"]))
    assert [(b["label"], b["count"]) for b in r["bars"]] == [("4", 1), ("5", 2), ("6", 0), ("7", 0), ("8", 0), ("9", 1)]
    assert (r["median"], r["lowest"], r["highest"], r["unit"], r["votes"]) == (5, 4, 9, "years", 4)
    assert report_for("number", r)["summary"] == "4 answers · median 5"
    assert value_for("number", {"value": 3.5}) == "3.5"


def test_number_wide_or_decimal_answers_get_round_ranges() -> None:
    r = result_for("number", {}, msgs("0", "100", "50", "12.5"))
    assert [(b["label"], b["count"]) for b in r["bars"]] == [("0–20", 2), ("20–40", 0), ("40–60", 1), ("60–80", 0), ("80–100", 1)]
    assert r["median"] == 31.25
    whole = result_for("number", {}, msgs("3", "5", "12", "7", "2", "20", "5", "10"))
    assert [(b["label"], b["count"]) for b in whole["bars"]] == [("0–4", 2), ("5–9", 3), ("10–14", 2), ("15–20", 1)]
    tiny = result_for("number", {}, msgs("0.1", "0.15", "0.3"))
    assert sum(b["count"] for b in tiny["bars"]) == 3 and len(tiny["bars"]) <= 8


# ------------------------------------------------------------------- all types


@pytest.mark.parametrize("kind", ["choice", "stars", "nps", "number"])
def test_each_poll_type_previews_from_its_samples_and_splits_phone_answers(kind: str) -> None:
    spec = editors()[kind]
    assert spec["has_stage"] and spec["capture"]
    r = preview(kind, {})
    assert r is not None and r["votes"] > 0
    phones = result_for(kind, {}, msgs(*spec["samples"][:2], room=True) + msgs("5", senders=["Chat"]))
    assert phones["sources"]["room"] == 2
