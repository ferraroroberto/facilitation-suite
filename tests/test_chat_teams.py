"""The Teams chat source (#237): the tree parser and the reader's Teams path,
on a synthetic tree in the shape the UI Automation probe recorded."""

from __future__ import annotations

import sys
import types
from datetime import datetime
from typing import Optional

import pytest

import src.chat
from src.chat.parse import Row
from src.chat.reader import Reader
from src.chat.teams import UNode, candidates, rows_from_tree, time_from_id
from tests.fixtures.teams_chat import as_list, card, teams_tree

T0 = 1788547260000  # an epoch-ms message id


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


# ------------------------------------------------------------------- parsing


def test_rows_from_tree_reads_sender_text_and_time() -> None:
    nodes = teams_tree([
        ("control", T0, "", "Meeting started"),
        ("other", T0 + 1000, "Alex Rivera", "Sevilla, España"),
        ("other", T0 + 61000, "Sam Taylor", "perfeccionismo"),
        ("own", T0 + 62000, "Facilitator Name", "the prompt"),
        ("control", T0 + 99000, "", "Meeting ended: after 49 minutes"),
    ])
    pairs, found = rows_from_tree(nodes)
    assert found
    assert [mid for mid, _ in pairs] == [str(T0 + 1000), str(T0 + 61000), str(T0 + 62000)]
    assert [r for _, r in pairs] == [
        Row("Alex Rivera", "Sevilla, España", hhmm(T0 + 1000)),
        Row("Sam Taylor", "perfeccionismo", hhmm(T0 + 61000)),
        Row("You", "the prompt", hhmm(T0 + 62000)),  # the facilitator: Zoom's "You", never an answer
    ]


def test_sidebar_previews_and_headings_are_not_messages() -> None:
    pairs, _ = rows_from_tree(teams_tree([("other", T0, "Alex Rivera", "hi")]))
    assert [r.text for _, r in pairs] == ["hi"]  # not the sidebar's preview, not "hi by Alex Rivera"


def test_a_card_reads_its_lines() -> None:
    nodes = teams_tree([], extra=card(4, T0, "Alex Rivera", ["Invited you to join this meeting", "2:15 PM - 2:45 PM"]))
    assert [r.text for _, r in rows_from_tree(nodes)[0]] == ["Invited you to join this meeting 2:15 PM - 2:45 PM"]


def test_no_chat_list() -> None:
    assert rows_from_tree([UNode(0, "", "RootWebArea", "Teams and Channels | Microsoft Teams")]) == ([], False)
    assert rows_from_tree(as_list(teams_tree([])))[1] is True


@pytest.mark.parametrize("bad", ["", "abc", "42", "99999999999999"])
def test_time_from_id_needs_epoch_ms(bad: str) -> None:
    assert time_from_id(bad) == ""


def test_candidates_prefer_an_open_chat() -> None:
    titles = [(1, "Launcher - Google Chrome"), (2, "Calendar | Microsoft Teams"),
              (3, "Chat | Weekly workshop | Microsoft Teams - Google Chrome"), (4, "Notes")]
    assert candidates(titles, "Microsoft Teams") == [3, 2]
    assert candidates(titles, "") == []


# ---------------------------------------------------------- the reader's path


class _Collect:
    def __init__(self) -> None:
        self.batches: list[tuple[list[Row], bool, str]] = []

    def messages(self, rows: list[Row], *, baseline: bool, source: str) -> None:
        self.batches.append((list(rows), baseline, source))


class _FakeTeams:
    """Stands in for ``src.chat.uia``: one window whose chat list is ``tree``."""

    def __init__(self) -> None:
        self.title: Optional[str] = "Chat | Weekly workshop | Microsoft Teams - Google Chrome"
        self.tree: Optional[list[UNode]] = None

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("src.chat.uia")
        mod.visible_titles = lambda: [(7, self.title)] if self.title else []  # type: ignore[attr-defined]
        mod.chat_list = lambda hwnd: as_list(self.tree) if self.tree is not None else None  # type: ignore[attr-defined]
        return mod


@pytest.fixture
def fake_teams(monkeypatch: pytest.MonkeyPatch) -> _FakeTeams:
    fake = _FakeTeams()
    mod = fake.module()
    monkeypatch.setitem(sys.modules, "src.chat.uia", mod)
    monkeypatch.setattr(src.chat, "uia", mod, raising=False)
    return fake


def test_reader_reads_teams_baseline_then_only_new(fake_teams: _FakeTeams) -> None:
    sink = _Collect()
    reader = Reader(sink, "", "", 500, None, source="teams")  # type: ignore[arg-type]
    history = [("other", T0, "Alex Rivera", "from an earlier meeting")]
    fake_teams.tree = teams_tree(history)
    reader.read_once()
    assert reader.state == "reading"
    assert sink.batches == [([Row("Alex Rivera", "from an earlier meeting", hhmm(T0))], True, "teams")]

    burst = [("other", T0 + 1000 + i, f"Person {i % 7}", f"answer {i}") for i in range(30)]
    fake_teams.tree = teams_tree(history + burst + [("other", T0 + 2000, "Sam Taylor", "same"), ("other", T0 + 2001, "Sam Taylor", "same")])
    reader.read_once()
    fresh = [r.text for rows, base, _ in sink.batches if not base for r in rows]
    assert fresh == [f"answer {i}" for i in range(30)] + ["same", "same"]
    assert all(src == "teams" for _, _, src in sink.batches)


def test_reader_takes_another_chat_as_history(fake_teams: _FakeTeams) -> None:
    sink = _Collect()
    reader = Reader(sink, "", "", 500, None, source="teams")  # type: ignore[arg-type]
    fake_teams.tree = teams_tree([("other", T0, "Alex Rivera", "one")])
    reader.read_once()
    # the facilitator opens another chat: nothing in common with the last read
    fake_teams.tree = teams_tree([("other", T0 - 5000, "Kim Ito", "old"), ("other", T0 - 4000, "Kim Ito", "older")])
    reader.read_once()
    fake_teams.tree = teams_tree([("other", T0 - 5000, "Kim Ito", "old"), ("other", T0 - 4000, "Kim Ito", "older"),
                                  ("other", T0 + 9000, "Sam Taylor", "new here")])
    reader.read_once()
    assert [r.text for rows, base, _ in sink.batches if not base for r in rows] == ["new here"]


def test_reader_teams_window_states(fake_teams: _FakeTeams) -> None:
    reader = Reader(_Collect(), "", "", 500, None, source="teams")  # type: ignore[arg-type]
    fake_teams.title = None
    reader.read_once()
    assert reader.state == "window_not_found" and "active tab" in reader.detail
    fake_teams.title = "Calendar | Microsoft Teams"
    reader.read_once()
    assert reader.state == "window_not_found" and "open the meeting chat" in reader.detail
    fake_teams.title, fake_teams.tree = "Chat | Weekly workshop | Microsoft Teams", teams_tree([])
    reader.read_once()
    assert (reader.state, reader.detail) == ("reading", "0 messages in the window")
