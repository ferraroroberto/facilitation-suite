"""The session room: one PIN per live session, members with a reconnect-safe identity, and a PIN
that survives a restart but dies when the room closes."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import load_config
from src.jsonl import read_jsonl
from src.live.hub import LiveHub
from src.room.model import Room, replay
from src.room.service import ROOM_FILE, RoomService
from src.sessions.store import SessionStore
from tests.fixtures.demo import build_demo_session


@pytest.fixture
def session(isolated_env: Path) -> tuple[str, Path]:
    return build_demo_session(isolated_env / "sessions" / "demo" / "room", isolated_env / "sessions.local.yaml")


def live_room(sid: str) -> tuple[LiveHub, RoomService]:
    hub = LiveHub(SessionStore(load_config()))
    room = RoomService(hub, clock=lambda: 1_800_000_000_000)
    hub.activate(sid)
    return hub, room


def other_pin(pin: str) -> str:
    return "111111" if pin != "111111" else "222222"


def test_a_session_going_live_opens_one_room_with_a_pin(session) -> None:
    sid, folder = session
    hub, room = live_room(sid)
    pin = room.pin()
    assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"
    assert hub.snapshot()["state"]["room"] == {"room_id": room.room.room_id, "pin": pin, "open": True, "member_count": 0}
    assert room.pin_matches(f"{pin[:3]} {pin[3:]}") and not room.pin_matches(other_pin(pin))
    assert [r["op"] for r in read_jsonl(folder / "live" / ROOM_FILE)] == ["open"]


def test_join_named_or_anonymous_and_a_retry_is_the_same_member(session) -> None:
    sid, _ = session
    _, room = live_room(sid)
    ana = room.join("  Ana   Lopez ", key="k-1")
    anon = room.join("")
    assert (ana.state, ana.name) == ("joined", "Ana Lopez") and (anon.state, anon.name) == ("joined", "")
    assert ana.player_id != anon.player_id and ana.secret != anon.secret
    retry = room.join("whatever", key="k-1")
    assert (retry.player_id, retry.secret) == (ana.player_id, ana.secret)
    assert len(room.room.members) == 2


def test_resume_needs_the_secret(session) -> None:
    sid, _ = session
    _, room = live_room(sid)
    ana = room.join("Ana")
    assert room.resume(ana.player_id, ana.secret).state == "resumed"
    assert room.resume(ana.player_id, "wrong").state == "unknown_player"
    assert room.resume("p-nobody", ana.secret).state == "unknown_player"


def test_the_pin_and_the_members_survive_a_restart(session) -> None:
    sid, _ = session
    _, room = live_room(sid)
    pin, ana = room.pin(), room.join("Ana")
    _, room2 = live_room(sid)  # a fresh server on the same folder: the session resumes live
    assert room2.pin() == pin
    back = room2.resume(ana.player_id, ana.secret)
    assert (back.state, back.name) == ("resumed", "Ana")


def test_closing_the_session_kills_the_pin_and_going_live_again_opens_a_new_room(session) -> None:
    sid, folder = session
    hub, room = live_room(sid)
    pin, ana = room.pin(), room.join("Ana")
    hub.deactivate()
    assert room.pin() == "" and not room.pin_matches(pin)
    assert room.join("Bo").state == "closed"
    records = read_jsonl(folder / "live" / ROOM_FILE)
    assert records[-1]["op"] == "close"
    assert not replay(records).open  # closed on disk too, so a restart cannot bring the PIN back

    hub.activate(sid)
    assert room.pin() not in ("", pin) and room.pin_matches(room.pin()) and not room.pin_matches(pin)
    assert room.resume(ana.player_id, ana.secret).state == "unknown_player"  # the old room's members are gone


def test_a_reset_sets_the_room_aside_and_opens_a_fresh_one(session) -> None:
    sid, folder = session
    hub, room = live_room(sid)
    pin, ana = room.pin(), room.join("Ana")
    hub.reset()
    assert room.pin() and room.pin() != pin and room.member(ana.player_id) is None
    assert [r["op"] for r in read_jsonl(folder / "live" / ROOM_FILE)] == ["open"]


def test_a_closed_room_takes_no_more_members_on_replay() -> None:
    room = Room("r1")
    for rec in ({"op": "open", "pin": "123456"}, {"op": "close"},
                {"op": "join", "player_id": "p1", "name": "", "secret": "s"}):
        room.apply(rec)
    assert not room.open and room.members == {}
