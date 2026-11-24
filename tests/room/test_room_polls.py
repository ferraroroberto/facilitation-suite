"""Room polls: phones in the session room answer the word cloud or scale on stage through the player
API, into the same capture as the chat — counted together, the split on the stage, a retry never
counted twice, a changed answer replacing the old one until the poll closes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.player.server import create_player_app
from src.live.capture import select_messages
from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import add_quiz_section


@pytest.fixture
def rig(client: TestClient, isolated_env) -> Iterator[tuple[TestClient, TestClient, str]]:
    """The main app live on the demo session, the player app over its services, and the room's PIN."""
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "polls", isolated_env / "sessions.local.yaml")
    add_quiz_section(folder)
    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    app = create_player_app(client.app.state.quiz, client.app.state.polls)
    with TestClient(app, client=("127.0.0.1", 50001)) as phone:
        yield client, phone, client.get("/api/live").json()["state"]["room"]["pin"]
    app.state.detach()


def goto(client: TestClient, item_id: str) -> None:
    item = next(it for it in client.get("/api/live").json()["plan"]["run"]["items"] if it["id"] == item_id)
    assert client.post(f"/api/actions/goto/{item['index'] + 1}").status_code == 200


def join(phone: TestClient, pin: str, name: str = "", key: str | None = None) -> dict[str, Any]:
    body = phone.post("/play/api/join", json={"pin": pin, "nickname": name, "key": key}).json()
    assert body["state"] == "joined", body
    return body


def send(phone: TestClient, me: dict[str, Any], item_id: str, value: Any, key: str | None = None) -> dict[str, Any]:
    r = phone.post("/play/api/poll", json={"player_id": me["player_id"], "secret": me["secret"], "item_id": item_id,
                                           "value": str(value), "key": key})
    return {"status": r.status_code, **r.json()}


def view(phone: TestClient, me: dict[str, Any]) -> dict[str, Any]:
    return phone.get(f"/play/api/state?player_id={me['player_id']}&secret={me['secret']}").json()["view"]


def capture(client: TestClient) -> dict[str, Any]:
    return client.get("/api/live").json()["state"]["capture"]


def chat(client: TestClient, *lines: tuple[str, str], batch: str) -> None:
    msgs = [{"sender": s, "text": t, "time": "18:41"} for s, t in lines]
    assert client.post("/api/chat/messages", json={"batch": batch, "messages": msgs}).status_code == 200


def test_phones_join_without_a_name_and_see_the_word_cloud_control(rig) -> None:
    client, phone, pin = rig
    assert phone.get("/play/api/ping").json()["name_required"] is False  # no quiz on stage
    anon = join(phone, pin)
    assert anon["name"] == "" and anon["view"]["view"]["kind"] == "room"
    back = phone.post("/play/api/resume", json={"player_id": anon["player_id"], "secret": anon["secret"]}).json()
    assert (back["state"], back["player_id"], back["view"]["view"]["kind"]) == ("resumed", anon["player_id"], "room")
    goto(client, "act-kryptonite")
    v = view(phone, anon)
    assert {k: v[k] for k in ("kind", "item_id", "control", "open", "answer", "max_length")} == {
        "kind": "poll", "item_id": "act-kryptonite", "control": "text", "open": False, "answer": None, "max_length": 80}
    assert "kryptonite" in v["question"]
    # before the capture starts the poll is not open: nothing is counted
    assert send(phone, anon, "act-kryptonite", "meetings")["state"] == "not_open"
    assert client.app.state.chat.messages == []


def test_phone_and_chat_answers_count_together_and_the_stage_shows_the_split(rig) -> None:
    client, phone, pin = rig
    goto(client, "act-kryptonite")
    assert client.post("/api/actions/capture_toggle").status_code == 200
    ana, anon = join(phone, pin, "Ana"), join(phone, pin)
    ack = send(phone, ana, "act-kryptonite", "  meetings ", key="a1")
    assert (ack["status"], ack["state"], ack["answer"]) == (200, "accepted", "meetings")
    assert send(phone, anon, "act-kryptonite", "meetings", key="b1")["state"] == "accepted"
    chat(client, ("Sam", "perfectionism"), ("Jo", "meetings"), batch="c1")
    cap = capture(client)
    assert (cap["answers"], cap["people"], cap["room"]) == (4, 4, 2)
    result = cap["result"]
    assert result["sources"] == {"room": 2, "chat": 2}
    assert {w["text"]: w["count"] for w in result["words"]} == {"meetings": 3, "perfectionism": 1}
    senders = [m["sender"] for m in client.app.state.chat.messages if m["source"] == "room"]
    assert senders == ["Ana", f"Guest {anon['player_id'][-4:]}"]  # an anonymous phone is its own person
    assert view(phone, ana)["answer"] == "meetings"


def test_a_retry_is_never_counted_twice(rig) -> None:
    client, phone, pin = rig
    goto(client, "act-kryptonite")
    client.post("/api/actions/capture_toggle")
    ana = join(phone, pin, "Ana")
    for _ in range(3):  # the phone lost the ack and sends the same answer with the same key
        assert send(phone, ana, "act-kryptonite", "focus", key="same")["state"] == "accepted"
    assert [m["text"] for m in client.app.state.chat.messages] == ["focus"]
    client.post("/api/actions/capture_toggle")  # closed: a late retry still gets its ack, nothing new counts
    assert send(phone, ana, "act-kryptonite", "focus", key="same")["state"] == "accepted"
    assert capture(client)["answers"] == 1


def test_a_changed_answer_replaces_the_old_one_until_the_poll_closes(rig) -> None:
    client, phone, pin = rig
    goto(client, "act-weather")
    me = join(phone, pin, "Ana")
    v = view(phone, me)
    assert (v["control"], v["min"], v["max"]) == ("scale", 1, 5)
    assert v["labels"] == ["storm", "rain", "cloudy", "sunny spells", "sun"]
    client.post("/api/actions/capture_toggle")
    assert send(phone, me, "act-weather", 2, key="k1")["answer"] == "2"
    assert send(phone, me, "act-weather", 5, key="k2")["answer"] == "5"
    cap = capture(client)
    assert (cap["answers"], cap["people"]) == (1, 1)
    assert cap["result"]["average"] == 5
    assert send(phone, me, "act-weather", 9, key="k3")["status"] == 422  # outside the scale
    client.post("/api/actions/capture_toggle")  # the poll closes
    late = send(phone, me, "act-weather", 3, key="k4")
    assert (late["status"], late["state"], late["answer"]) == (200, "not_open", "5")
    v = view(phone, me)
    assert (v["open"], v["answer"]) == (False, "5")
    frozen = client.app.state.capture.frozen("act-weather")
    assert [a["text"] for a in frozen["answers"]] == ["5"] and frozen["result"]["average"] == 5


def test_select_messages_keeps_a_phones_newest_answer_only() -> None:
    msgs = [{"id": 1, "sender": "Ana", "text": "2", "received_at": 10, "source": "room", "player_id": "p1"},
            {"id": 2, "sender": "Sam", "text": "3", "received_at": 11, "source": "zoom"},
            {"id": 3, "sender": "Ana", "text": "5", "received_at": 12, "source": "room", "player_id": "p1"},
            {"id": 4, "sender": "Ana", "text": "1", "received_at": 99, "source": "room", "player_id": "p1"}]
    # the answer after the window closed is not counted, so it replaces nothing either
    assert [m["id"] for m in select_messages(msgs, [[0, 50]], hidden=set(), places={})] == [2, 3]


def test_the_quiz_chat_fallback_ignores_phone_poll_answers(rig) -> None:
    client, _, _ = rig
    # no game and no question: a room message returns before either is read, so nothing is scored
    client.app.state.quiz_chat._one(None, None, {"id": 1, "sender": "Ana", "text": "B", "source": "room", "own": False})


def test_an_unknown_phone_is_refused(rig) -> None:
    client, phone, _ = rig
    goto(client, "act-kryptonite")
    client.post("/api/actions/capture_toggle")
    assert send(phone, {"player_id": "pnobody", "secret": "x"}, "act-kryptonite", "hi")["state"] == "unknown_player"
    assert phone.get("/play/api/state?player_id=pnobody&secret=x").json()["state"] == "unknown_player"


def test_a_room_member_joins_a_quiz_later_as_the_same_phone(rig) -> None:
    client, phone, pin = rig
    me = join(phone, pin, key="phone-1")  # anonymous, no quiz on stage
    goto(client, "pq-lobby")
    assert phone.get("/play/api/ping").json()["name_required"] is True
    assert view(phone, me) == {"kind": "room", "lang": "en", "quiz_join": True}
    r = phone.post("/play/api/join", json={"pin": pin, "nickname": "", "key": "phone-1"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_nickname"  # the quiz still needs one
    player = join(phone, pin, "Ana", key="phone-1")
    assert (player["player_id"], player["secret"], player["game_id"]) == (me["player_id"], me["secret"], "pq-lobby-1")
    assert view(phone, me)["phase"] == "lobby"
    resumed = phone.post("/play/api/resume", json={"player_id": me["player_id"], "secret": me["secret"]}).json()
    assert resumed["state"] == "resumed"
