"""The chat: row parsing, order-based diffing, the hub, the API, and a 50-message
burst read through real MSAA from a stand-in chat window."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.chat.hub import ChatHub
from src.chat.parse import Row, new_rows, parse_row
from src.chat.reader import Reader, message_names
from src.config import load_config
from src.live.hub import LiveHub, now_ms
from src.sessions.store import SessionStore
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
    chat.heartbeat({"state": "reading", "detail": "3 messages", "rows": 3})
    assert chat.readiness()["tested"] is True
    chat.last_beat_ms = now_ms() - 5000
    assert chat.reader_state()[0] == "stale"
    chat.process_running = lambda: False
    assert chat.reader_state()[0] == "off"


def test_chat_api_is_loopback_only_for_the_reader(isolated_env: Path) -> None:
    from fastapi.testclient import TestClient

    from app.webapp.server import create_app

    body = {"batch": "x-1", "messages": [{"sender": "Sam", "text": "hi", "time": "18:41"}]}
    with TestClient(create_app(), client=("10.0.0.5", 5000)) as remote:
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
