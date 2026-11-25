"""The Room Polls demo session: it carries every feature the owner's phone test walks
through, every poll in it is phone-answerable, and the script loads it into a running app."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from scripts import room_polls_demo
from src.forms.model import questions
from src.live.plan import build_run
from src.room.controls import control_of
from src.sessions.model import parse_session
from tests.fixtures.room_polls_demo import POLL_TYPES, build_room_polls_demo, demo_items, load_plan


def _of(kind: str) -> list[dict[str, Any]]:
    return [it for it in demo_items() if it.get("type") == kind]


def test_the_demo_carries_every_feature() -> None:
    assert demo_items()[0]["type"] == "room_join"  # phones join before anything else
    assert [it["type"] for it in demo_items() if str(it.get("type", "")).startswith("quiz")] == \
        ["quiz_lobby", "quiz", "quiz", "quiz_podium"]
    for kind in POLL_TYPES:
        assert _of(kind), kind
    assert [bool((it.get("options") or {}).get("multiple")) for it in _of("choice")] == [False, True]  # choice and multi choice
    scale = _of("scale")[0]["options"]
    assert (scale["min"], scale["max"]) == (0, 10)
    form = _of("form")
    assert len(form) == 1 and len(questions(form[0]["options"])) == 5


def test_every_poll_is_answerable_on_a_phone() -> None:
    run = build_run(parse_session(load_plan()), None)
    polls = [it for it in run["items"] if it["type"] in POLL_TYPES]
    assert len(polls) == len(POLL_TYPES) + 1  # the multi choice is a second choice item
    assert all(control_of(it) for it in polls), [it["id"] for it in polls if not control_of(it)]


def test_the_demo_folder_opens_in_the_app(client: TestClient, tmp_path, isolated_env) -> None:
    sid, _ = build_room_polls_demo(tmp_path / "demo-room-polls", isolated_env / "sessions.local.yaml")
    plan = client.get(f"/api/sessions/{sid}")
    assert plan.status_code == 200
    assert plan.json()["session"]["title"] == "Room Polls demo"


def test_the_script_creates_then_resets_the_demo(client: TestClient, monkeypatch) -> None:
    def call(_base: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        resp = client.request(method, path, json=body)
        return resp.status_code, resp.json()

    monkeypatch.setattr(room_polls_demo, "_call", call)
    sid = room_polls_demo.load_demo("")
    session = client.get(f"/api/sessions/{sid}").json()["session"]
    assert [sec["id"] for sec in session["sections"]] == [sec["id"] for sec in load_plan()["sections"]]
    # a second run finds the same session and puts the plan back, whatever was edited meanwhile
    client.put(f"/api/sessions/{sid}", json={"session": {**session, "sections": session["sections"][:1]}})
    assert room_polls_demo.load_demo("") == sid
    assert len(client.get(f"/api/sessions/{sid}").json()["session"]["sections"]) == len(load_plan()["sections"])
