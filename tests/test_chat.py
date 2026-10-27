"""The chat: row parsing, order-based diffing, the hub, the API, and a 50-message
burst read through real MSAA from a stand-in chat window."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from src.chat.hub import ChatHub
from src.chat.parse import Row, new_rows, parse_row
from src.chat.reader import Reader, message_names
from src.config import load_config
from src.live.actions import run_action
from src.live.capture import CaptureService
from src.live.hub import LiveHub, now_ms
from src.sessions.store import SessionStore
from tests.conftest import paired_remote
from tests.fixtures.demo import build_demo_session

# ------------------------------------------------------------------- parsing


@pytest.mark.parametrize(("name", "expected"), [
    ("Ana Coronel, , Sevilla, España, 14:07, ", ("Ana Coronel", "Sevilla, España", "14:07")),
    ("Sam, , perfeccionismo, 18:41, ", ("Sam", "perfeccionismo", "18:41")),
    ("Sam, , 4 , 18:41, ", ("Sam", "4", "18:41")),  # an emoji after "4" arrives as nothing
    ("You, , hola a todos, 18:40, ", ("You", "hola a todos", "18:40")),
    ("Robin, , a, b, c, 9:05 PM, ", ("Robin", "a, b, c", "9:05 PM")),
])
def test_parse_row(name: str, expected: tuple[str, str, str]) -> None:
    r = parse_row(name)
    assert r is not None and (r.sender, r.text, r.time) == expected


@pytest.mark.parametrize("name", ["Today, ", "13:58 - meeting started", "", "Ana Coronel, 2 of 3, 1 unread message"])
def test_system_and_private_rows_are_dropped(name: str) -> None:
    assert parse_row(name) is None


def _rows(*texts: str) -> list[Row]:
    return [Row("P", t, "18:41") for t in texts]


def test_new_rows_by_order() -> None:
    assert new_rows([], _rows("a", "b"))[0] == _rows("a", "b")
    assert new_rows(_rows("a", "b"), _rows("a", "b", "c", "d")) == (_rows("c", "d"), "append")
    # the oldest rows left the tree
    assert new_rows(_rows("x", "a", "b"), _rows("a", "b", "c")) == (_rows("c"), "append")
    # the same person, the same text, the same minute: still two messages
    assert new_rows(_rows("a", "a"), _rows("a", "a", "a")) == (_rows("a"), "append")
    assert new_rows(_rows("a"), _rows("a")) == ([], "append")


def test_new_rows_realigns_after_a_deleted_message() -> None:
    fresh, how = new_rows(_rows("a", "b", "c"), _rows("a", "c", "d"))
    assert (fresh, how) == (_rows("d"), "realign")


def test_message_names_prefers_the_history_list() -> None:
    from src.chat.msaa import Node

    nodes = [Node(33, "Participants", 1), Node(34, "Ana, , not a message, 18:00, ", 2),
             Node(33, "Chat messages history , Ctrl+Shift+U", 1), Node(34, "Sam, , hi, 18:01, ", 2), Node(9, "", 1)]
    assert message_names(nodes) == (["Sam, , hi, 18:01, "], True)
    assert message_names(nodes[:2]) == (["Ana, , not a message, 18:00, "], False)


# ----------------------------------------------------------------------- hub


@pytest.fixture
def live_demo(isolated_env: Path) -> tuple[LiveHub, ChatHub, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "chat", isolated_env / "sessions.local.yaml")
    live = LiveHub(SessionStore(load_config()))
    chat = ChatHub(live)
    live.activate(sid)
    return live, chat, folder


def _msg(sender: str, text: str, time: str = "18:41") -> dict[str, Any]:
    return {"sender": sender, "text": text, "time": time, "received_at": now_ms()}


def test_ingest_writes_chat_jsonl_and_dedupes_batches(live_demo) -> None:
    live, chat, folder = live_demo
    sent: list[dict] = []
    client = live.connect("presenter")
    client.push = sent.append  # type: ignore[method-assign]
    stored = chat.ingest("r1-1", [_msg("Sam", "meetings"), _msg("You", "the prompt")], baseline=False, source="zoom")
    assert [m["id"] for m in stored] == [1, 2] and stored[1]["own"] is True
    assert chat.ingest("r1-1", [_msg("Sam", "meetings")], baseline=False, source="zoom") == []  # a retried batch
    lines = (folder / "live" / "chat.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["text"] for x in lines] == ["meetings", "the prompt"]
    assert any(m["type"] == "chat" and m["count"] == 2 for m in sent)
    # a restart reads the file back
    fresh = ChatHub(live)
    assert [m["text"] for m in fresh.since(0)] == ["meetings", "the prompt"]


def test_a_failed_chat_append_is_kept_and_written_with_the_next_one(live_demo, fail_append_once) -> None:
    live, chat, folder = live_demo
    sent: list[dict] = []
    client = live.connect("presenter")
    client.push = sent.append  # type: ignore[method-assign]
    fail_append_once("chat.jsonl")
    chat.ingest("b1", [_msg("Sam", "silos")], baseline=False, source="zoom")
    warning = "Could not write chat.jsonl in the session folder — kept in memory"
    assert live.write_error == warning
    assert [m["state"]["write_error"] for m in sent if m["type"] == "state"][-1] == warning
    chat.ingest("b2", [_msg("Ana", "meetings")], baseline=False, source="zoom")
    assert live.write_error is None
    assert [m["state"]["write_error"] for m in sent if m["type"] == "state"][-1] is None
    lines = (folder / "live" / "chat.jsonl").read_text(encoding="utf-8").splitlines()
    assert [(json.loads(x)["id"], json.loads(x)["text"]) for x in lines] == [(1, "silos"), (2, "meetings")]


def test_a_file_written_again_keeps_the_warning_of_another(live_demo, fail_append_once) -> None:
    live, chat, _ = live_demo
    fail_append_once("events.jsonl")
    live.goto(2)
    fail_append_once("chat.jsonl")
    chat.ingest("b1", [_msg("Sam", "silos")], baseline=False, source="zoom")
    chat.ingest("b2", [_msg("Ana", "meetings")], baseline=False, source="zoom")  # chat.jsonl is fine again
    assert live.write_error == "Could not write events.jsonl in the session folder — kept in memory"
    live.goto(3)
    assert live.write_error is None


def test_a_damaged_line_skips_only_itself_and_ids_never_collide(live_demo) -> None:
    live, chat, folder = live_demo
    chat.ingest("d-1", [_msg("Sam", "one"), _msg("Ana", "two"), _msg("Kim", "three")], baseline=False, source="zoom")
    path = folder / "live" / "chat.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1][:20]  # a line cut short (a crash mid-write): not JSON any more
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fresh = ChatHub(live)  # a restart reads the file back
    assert [m["text"] for m in fresh.since(0)] == ["one", "three"]  # every good line, also after the damage
    stored = fresh.ingest("d-2", [_msg("Lee", "four")], baseline=False, source="zoom")
    assert [m["id"] for m in stored] == [4]  # after the highest id read, never a second 3


def test_baseline_is_history_unless_it_follows_stored_rows(live_demo) -> None:
    _, chat, _ = live_demo
    # a reader starting on a window full of earlier chat: nothing is new
    assert chat.ingest("a-1", [_msg("Old", "from last week")], baseline=True, source="zoom") == []
    chat.ingest("a-2", [_msg("Sam", "one")], baseline=False, source="zoom")
    # the reader restarts; meanwhile "two" and "three" arrived
    rows = [_msg("Old", "from last week"), _msg("Sam", "one"), _msg("Sam", "two"), _msg("Sam", "three")]
    assert [m["text"] for m in chat.ingest("b-1", rows, baseline=True, source="zoom")] == ["two", "three"]


def test_reader_state_machine(live_demo, monkeypatch: pytest.MonkeyPatch) -> None:
    _, chat, _ = live_demo
    assert chat.reader_state()[0] == "off"
    chat.process_running = lambda: True
    assert chat.reader_state()[0] == "starting"
    chat.heartbeat({"state": "window_not_found", "detail": "Pop out the Zoom meeting chat"})
    assert chat.reader_state() == ("window_not_found", "Pop out the Zoom meeting chat")
    chat.heartbeat({"state": "reading", "detail": "3 messages", "rows": 3, "source": "zoom"})
    assert chat.readiness()["tested"] is True
    chat.last_beat_ms = now_ms() - 5000
    assert chat.reader_state()[0] == "stale"
    chat.process_running = lambda: False
    assert chat.reader_state()[0] == "off"


def test_readiness_names_the_chat_of_the_chosen_source(live_demo) -> None:
    """#256: Teams has no pop-out, so a passed test must not say "popped-out chat"."""
    _, chat, _ = live_demo
    assert chat.readiness()["source"] == "zoom"
    chat.chosen_source = lambda: "teams"
    assert chat.readiness()["source"] == "teams"
    chat.process_running = lambda: True
    chat.heartbeat({"state": "reading", "detail": "3 messages", "rows": 3, "source": "teams"})
    got = chat.readiness()
    assert got["tested"] is True and got["source"] == "teams"
    assert "meeting chat" in got["detail"] and "popped-out" not in got["detail"]
    chat.chosen_source = lambda: "zoom"
    chat.heartbeat({"state": "reading", "detail": "1 message", "rows": 1, "source": "zoom"})
    assert "popped-out chat" in chat.readiness()["detail"]


def test_readiness_tested_flag_does_not_survive_a_source_switch(live_demo) -> None:
    """#298: a passed test on one source must not carry over to the other after
    the presenter's Zoom · Teams switch — the checklist would otherwise claim
    an untested source as read."""
    _, chat, _ = live_demo
    chat.process_running = lambda: True
    chat.heartbeat({"state": "reading", "detail": "3 messages", "rows": 3, "source": "zoom"})
    assert chat.readiness()["tested"] is True
    chat.chosen_source = lambda: "teams"
    assert chat.readiness()["tested"] is False  # the Teams reader was never read
    chat.heartbeat({"state": "reading", "detail": "2 messages", "rows": 2, "source": "teams"})
    assert chat.readiness()["tested"] is True
    chat.chosen_source = lambda: "zoom"
    assert chat.readiness()["tested"] is False  # switching back doesn't resurrect the old test either


def test_chat_api_is_loopback_only_for_the_reader(isolated_env: Path) -> None:
    from fastapi.testclient import TestClient

    from app.webapp.server import create_app

    body = {"batch": "x-1", "messages": [{"sender": "Sam", "text": "hi", "time": "18:41"}]}
    with paired_remote(create_app(), "10.0.0.5") as remote:  # even a paired phone cannot post as the reader
        assert remote.post("/api/chat/messages", json=body).json()["error"]["code"] == "local_only"
        assert remote.post("/api/chat/heartbeat", json={"state": "reading"}).status_code == 403
    with TestClient(create_app(), client=("127.0.0.1", 5000)) as local:
        assert local.post("/api/chat/messages", json=body).json() == {"stored": 1}
        got = local.get("/api/chat/messages").json()
        assert got["count"] == 1 and got["reader"]["state"] == "off"


# ------------------------------------------------------ MSAA burst (Windows)


class _Collect:
    def __init__(self) -> None:
        self.batches: list[tuple[list[Row], bool]] = []

    def messages(self, rows: list[Row], *, baseline: bool, source: str) -> None:
        self.batches.append((list(rows), baseline))

    @property
    def fresh(self) -> list[Row]:
        return [r for rows, base in self.batches if not base for r in rows]


@pytest.mark.skipif(sys.platform != "win32", reason="MSAA is Windows-only")
def test_a_50_message_burst_is_read_with_zero_loss() -> None:
    from tests.fixtures.fake_zoom_chat import FakeZoomChat, row

    chat = FakeZoomChat()
    try:
        chat.add("Today, ", row("Old Friend", "from an earlier meeting", "17:02"), "18:30 - meeting started")
        sink = _Collect()
        reader = Reader(sink, chat.window_class, chat.title, 500, None)  # type: ignore[arg-type]
        reader.read_once()
        assert reader.state == "reading" and sink.batches == [([Row("Old Friend", "from an earlier meeting", "17:02")], True)]

        burst = [row(f"Person {i % 40}", f"answer {i}, with a comma", "18:41") for i in range(50)]
        chat.add(*burst)
        chat.add(row("Person 1", "same", "18:42"), row("Person 1", "same", "18:42"))  # a genuine repeat
        reader.read_once()
        texts = [r.text for r in sink.fresh]
        assert texts == [f"answer {i}, with a comma" for i in range(50)] + ["same", "same"]

        chat.add(row("Person 2", "late one", "18:43"))
        reader.read_once()
        assert [r.text for r in sink.fresh][-1] == "late one" and len(sink.fresh) == 53
    finally:
        chat.close()
    reader.read_once()
    assert reader.state == "window_not_found"


# ------------------------------------------- a chat window that goes and comes back


class _FakeWindow:
    """The popped-out chat as the reader sees it through MSAA: open with ``rows``, or gone (``None``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.chat import msaa

        self.rows: Optional[list[Row]] = []
        monkeypatch.setattr(msaa, "find_window", lambda _cls, _title: 1 if self.rows is not None else None)
        monkeypatch.setattr(msaa, "accessible_from_window", lambda hwnd: hwnd)
        monkeypatch.setattr(msaa, "walk", lambda _acc: [msaa.Node(33, "Chat messages history", 1)]
                            + [msaa.Node(34, f"{r.sender}, , {r.text}, {r.time}, ", 2) for r in self.rows or []])


class _HubPoster:
    """Stands in for the HTTP Poster: each batch goes straight to ChatHub.ingest."""

    def __init__(self, chat: ChatHub) -> None:
        self.chat, self.n = chat, 0

    def messages(self, rows: list[Row], *, baseline: bool, source: str) -> None:
        if not rows and not baseline:
            return
        self.n += 1
        self.chat.ingest(f"t-{self.n}", [{"sender": r.sender, "text": r.text, "time": r.time, "received_at": now_ms()}
                                         for r in rows], baseline=baseline, source=source)


def _goto(live: LiveHub, item_id: str) -> None:
    live.goto(live.item_by_id(item_id)["index"])


def test_a_returning_chat_window_reposts_no_history_into_the_open_capture(
        live_demo, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Every breakout round: the chat list empties, the window goes, the facilitator pops it out again."""
    live, chat, _ = live_demo
    cap = CaptureService(live, chat)
    window = _FakeWindow(monkeypatch)
    reader = Reader(_HubPoster(chat), "cls", "Meeting chat", 500, None)  # type: ignore[arg-type]
    reader.read_once()  # baseline: an empty chat

    _goto(live, "act-kryptonite")
    run_action(live, "capture_toggle")
    time.sleep(0.003)
    history = [Row("Participant A", "courage", "19:16"), Row("Participant B", "listening", "19:16"),
               Row("Participant C", "humour", "19:17")]
    window.rows = list(history)
    reader.read_once()
    time.sleep(0.003)
    run_action(live, "capture_toggle")
    assert [m["text"] for m in cap.messages_for("act-kryptonite")] == ["courage", "listening", "humour"]

    _goto(live, "act-takeaway")
    for typed in ("first round answer", "second round answer"):
        window.rows = []
        reader.read_once()  # the list goes empty…
        window.rows = None
        reader.read_once()  # …then the window is gone
        run_action(live, "capture_toggle")  # the next activity's capture starts first
        time.sleep(0.003)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="chat_reader"):
            window.rows = list(history)
            reader.read_once()  # popped out again: the same history, nothing new typed
        assert [m["text"] for m in chat.messages] == [r.text for r in history]
        assert any("re-baselin" in r.getMessage() and f"{len(history)} rows" in r.getMessage() for r in caplog.records)
        history.append(Row("Participant D", typed, "19:47"))
        window.rows = list(history)
        reader.read_once()
        time.sleep(0.003)
        run_action(live, "capture_toggle")
    assert [m["text"] for m in cap.messages_for("act-takeaway")] == ["first round answer", "second round answer"]
    assert [m["text"] for m in chat.messages] == [r.text for r in history]  # every row stored once


def test_an_empty_snapshot_between_two_identical_ones_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    window = _FakeWindow(monkeypatch)
    sink = _Collect()
    reader = Reader(sink, "cls", "Meeting chat", 500, None)  # type: ignore[arg-type]
    rows = [Row("Participant A", "one", "18:20"), Row("Participant B", "two", "18:21")]
    window.rows = list(rows)
    reader.read_once()
    window.rows = []
    reader.read_once()
    window.rows = list(rows)
    reader.read_once()
    assert sink.fresh == []


def test_a_relabelled_sender_is_history_and_a_new_row_after_it_is_posted_once(live_demo) -> None:
    """A participant renaming themselves makes Zoom relabel their past rows."""
    _, chat, _ = live_demo
    prev = [Row("Participant A", "one", "18:20"), Row("Participant B", "two", "18:21")]
    cur = [Row("Participant A", "one", "18:20"), Row("Participant B (renamed)", "two", "18:21"),
           Row("Participant C", "three", "18:22")]
    assert new_rows(prev, cur) == ([cur[2]], "append")
    chat.ingest("a-1", [_msg(r.sender, r.text, r.time) for r in prev], baseline=False, source="zoom")
    stored = chat.ingest("b-1", [_msg(r.sender, r.text, r.time) for r in cur], baseline=True, source="zoom")
    assert [m["text"] for m in stored] == ["three"]


def test_a_batch_of_old_rows_leaves_a_warning(live_demo, caplog: pytest.LogCaptureFixture) -> None:
    _, chat, _ = live_demo
    now = time.localtime()
    hhmm = f"{now.tm_hour}:{now.tm_min:02d}"
    two_hours_ago = f"{(now.tm_hour - 2) % 24}:{now.tm_min:02d}"
    with caplog.at_level(logging.WARNING, logger="src.chat.hub"):
        chat.ingest("o-1", [_msg("Sam", "just typed", hhmm)], baseline=False, source="zoom")
        assert not any("history posted again" in r.getMessage() for r in caplog.records)
        chat.ingest("o-2", [_msg("Sam", "from earlier", two_hours_ago)], baseline=False, source="zoom")
    assert any("starts at " + two_hours_ago in r.getMessage() and "120 min ago" in r.getMessage() for r in caplog.records)


def test_a_snapshot_sharing_nothing_is_history_not_new_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    assert new_rows(_rows("a", "b"), [Row("Q", "elsewhere", "19:00")])[0] == []
    window = _FakeWindow(monkeypatch)
    sink = _Collect()
    reader = Reader(sink, "cls", "Meeting chat", 500, None)  # type: ignore[arg-type]
    window.rows = _rows("a", "b")
    reader.read_once()
    window.rows = [Row("Q", "a breakout room's chat", "19:00")]
    reader.read_once()
    assert sink.fresh == [] and sink.batches[-1] == ([Row("Q", "a breakout room's chat", "19:00")], True)
