"""The Google Meet chat source: the panel parser and the reader's Meet path, on
synthetic trees modelled on Meet's in-call chat panel (not recorded from a live call)."""

from __future__ import annotations

import sys
import time
import types
from typing import Optional

import pytest

import src.chat
from src.chat.meet import candidates, rows_from_tree, to_hhmm
from src.chat.parse import Row
from src.chat.reader import Reader
from src.chat.teams import UNode
from src.config import load_config
from tests.fixtures.meet_chat import PANEL, meet_tree

# ------------------------------------------------------------------- parsing


@pytest.mark.parametrize("layout", ["wrapped", "flat"])
def test_rows_from_tree_reads_each_message_under_its_header(layout: str) -> None:
    nodes = meet_tree([
        ("Alex Rivera", "10:42 AM", ["Sevilla, España", "perfeccionismo"]),
        ("You", "10:43 AM", ["the prompt"]),
        ("Sam Taylor", "1:05 PM", ["4"]),
    ], layout=layout)
    rows, found = rows_from_tree(nodes, PANEL)
    assert found
    assert rows == [
        Row("Alex Rivera", "Sevilla, España", "10:42"),
        Row("Alex Rivera", "perfeccionismo", "10:42"),
        Row("You", "the prompt", "10:43"),  # the facilitator: Zoom's "You", never an answer
        Row("Sam Taylor", "4", "13:05"),
    ]


@pytest.mark.parametrize("layout", ["wrapped", "flat"])
def test_system_lines_notices_buttons_tiles_and_captions_are_not_messages(layout: str) -> None:
    # A system line with a time, first in the list, must not read as a header over the whole list.
    nodes = meet_tree([("Alex Rivera", "10:42 AM", ["hi"]), ("Sam Taylor", "10:44 AM", ["hello"])], layout=layout,
                      system={0: "Chat was turned on for everyone", 1: "Kim Ito joined", 2: "Alex Rivera left the call"})
    assert [(r.sender, r.text) for r in rows_from_tree(nodes, PANEL)[0]] == [("Alex Rivera", "hi"), ("Sam Taylor", "hello")]


def test_a_message_split_over_text_nodes_is_one_row_and_an_emoji_is_none() -> None:
    nodes = meet_tree([("Alex Rivera", "10:42 AM", [["see", "example.org/notes"], ""]), ("Sam Taylor", "10:43 AM", [""])])
    assert rows_from_tree(nodes, PANEL)[0] == [Row("Alex Rivera", "see example.org/notes", "10:42")]


def test_no_panel_and_an_empty_panel() -> None:
    assert rows_from_tree(meet_tree([], panel=False), PANEL) == ([], False)
    assert rows_from_tree(meet_tree([]), PANEL) == ([], True)
    assert rows_from_tree(meet_tree([("Alex Rivera", "10:42 AM", ["hi"])]), "") == ([], False)


@pytest.mark.parametrize(("label", "expected"), [
    ("10:42 AM", "10:42"), ("12:05 AM", "00:05"), ("12:05 PM", "12:05"), ("1:05 p.m.", "13:05"),
    ("9:07 PM", "21:07"), ("14:07", "14:07"), ("", ""), ("Alex", ""), ("25:00", ""), ("13:00 PM", ""),
])
def test_to_hhmm(label: str, expected: str) -> None:
    assert to_hhmm(label) == expected


def test_candidates_prefer_a_meet_tab_and_skip_zoom() -> None:
    titles = [(1, "Meeting chat"), (2, "Zoom Meeting"), (3, "Google Meet - Google Chrome"),
              (4, "Meet - abc-defg-hij - Google Chrome"), (5, "Meet – Weekly workshop - Microsoft​ Edge"), (6, "Notes")]
    assert candidates(titles, "Meet") == [4, 5, 3]
    assert candidates(titles, "") == []


def test_a_large_page_parses_quickly() -> None:
    groups = [(f"Person {i % 40}", "10:42 AM", [f"answer {i}", f"more {i}"]) for i in range(400)]
    nodes = meet_tree(groups)
    nodes = nodes[:2] + [UNode(2 + d % 3, "", "", f"tile {d}", 50026) for d in range(3000)] + nodes[2:]
    started = time.perf_counter()
    rows, _ = rows_from_tree(nodes, PANEL)
    assert len(rows) == 800 and time.perf_counter() - started < 0.25  # two reads a second


# ---------------------------------------------------------- the reader's path


class _Collect:
    def __init__(self) -> None:
        self.batches: list[tuple[list[Row], bool, str]] = []

    def messages(self, rows: list[Row], *, baseline: bool, source: str) -> None:
        self.batches.append((list(rows), baseline, source))


class _FakeMeet:
    """Stands in for ``src.chat.uia``: one browser window whose page is ``tree``."""

    def __init__(self) -> None:
        self.title: Optional[str] = "Meet - abc-defg-hij - Google Chrome"
        self.tree: list[UNode] = meet_tree([], panel=False)

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("src.chat.uia")
        mod.visible_titles = lambda: [(7, self.title)] if self.title else []  # type: ignore[attr-defined]
        mod.meet_window = lambda hwnd, panel: (  # type: ignore[attr-defined]
            self.tree if any(n.name == panel for n in self.tree) else None)
        return mod


@pytest.fixture
def fake_meet(monkeypatch: pytest.MonkeyPatch) -> _FakeMeet:
    fake = _FakeMeet()
    mod = fake.module()
    monkeypatch.setitem(sys.modules, "src.chat.uia", mod)
    monkeypatch.setattr(src.chat, "uia", mod, raising=False)
    return fake


def _new(sink: _Collect) -> list[str]:
    return [r.text for rows, base, _ in sink.batches if not base for r in rows]


def test_reader_reads_meet_baseline_then_only_new(fake_meet: _FakeMeet) -> None:
    sink = _Collect()
    reader = Reader(sink, "", "", 500, None, source="meet")  # type: ignore[arg-type]
    history = [("Alex Rivera", "10:40 AM", ["before the reader started"])]
    fake_meet.tree = meet_tree(history)
    reader.read_once()
    assert reader.state == "reading"
    assert sink.batches == [([Row("Alex Rivera", "before the reader started", "10:40")], True, "meet")]

    # A burst: Meet groups the same sender's consecutive messages under one header.
    burst = [(f"Person {i}", "10:45 AM", [f"answer {i}a", f"answer {i}b"]) for i in range(15)]
    fake_meet.tree = meet_tree(history + burst + [("Sam Taylor", "10:46 AM", ["same", "same"])])
    reader.read_once()
    assert _new(sink) == [f"answer {i}{x}" for i in range(15) for x in "ab"] + ["same", "same"]
    assert all(src == "meet" for _, _, src in sink.batches)


def test_reopening_the_panel_never_reposts_history(fake_meet: _FakeMeet) -> None:
    sink = _Collect()
    reader = Reader(sink, "", "", 500, None, source="meet")  # type: ignore[arg-type]
    chat = [("Alex Rivera", "10:40 AM", ["one", "two"])]
    fake_meet.tree = meet_tree(chat)
    reader.read_once()
    fake_meet.tree = meet_tree(chat, panel=False)  # the facilitator closes the chat panel
    reader.read_once()
    assert reader.state == "window_not_found" and "open the chat panel" in reader.detail
    # Reopened: Meet may redraw only what it kept, plus what arrived meanwhile.
    fake_meet.tree = meet_tree([("Alex Rivera", "10:40 AM", ["two"]), ("Sam Taylor", "10:41 AM", ["while closed"])])
    reader.read_once()
    assert _new(sink) == []
    assert [base for _, base, _ in sink.batches] == [True, True]  # the server lines it up with what it stored
    fake_meet.tree = meet_tree([("Alex Rivera", "10:40 AM", ["two"]), ("Sam Taylor", "10:41 AM", ["while closed", "new"])])
    reader.read_once()
    assert _new(sink) == ["new"]


def test_reader_meet_window_states(fake_meet: _FakeMeet) -> None:
    reader = Reader(_Collect(), "", "", 500, None, source="meet")  # type: ignore[arg-type]
    fake_meet.title = None
    reader.read_once()
    assert reader.state == "window_not_found" and "active tab" in reader.detail
    fake_meet.title = "Meet - abc-defg-hij - Google Chrome"
    reader.read_once()
    assert reader.state == "window_not_found" and "open the chat panel" in reader.detail
    fake_meet.tree = meet_tree([])
    reader.read_once()
    assert (reader.state, reader.detail) == ("reading", "0 messages in the window")


def test_the_reader_takes_meets_labels_from_the_config(monkeypatch: pytest.MonkeyPatch, isolated_env) -> None:
    from src.chat import reader as reader_mod

    seen: list[tuple[str, str, str]] = []
    monkeypatch.setattr(reader_mod.sys, "platform", "win32")
    monkeypatch.setattr(reader_mod.Reader, "run", lambda self: seen.append((self.source, self.meet_title, self.meet_panel)) or 0)
    assert reader_mod.main(["--server", "http://127.0.0.1:1", "--source", "meet"]) == 0
    cfg = load_config().reader
    assert seen == [("meet", cfg.meet_title, cfg.meet_panel)] == [("meet", "Meet", "In-call messages")]
