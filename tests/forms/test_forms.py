"""Private feedback forms: the builder's form saves into the session, phones submit through the
public link (named, anonymous, twice), the backstage reads the aggregates and the CSV, the stage and
the phones' sockets never carry an answer, and a closed form says so."""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.player.server import create_player_app
from src.forms.model import FormError, clean_answers, questions
from src.forms.service import FORMS_FILE
from src.quiz.reach import OK, Reach
from tests.fixtures.demo import build_demo_session
from tests.fixtures.forms import FORM_QUESTIONS, FORM_SECTION
from tests.fixtures.quiz_plan import PLAYER_QUIZ, add_quiz_section

JOIN_BASE = "https://quiz.example.org"
# Distinctive answers: none of them may ever show up in a snapshot or a phone's view.
SECRET_TEXT = "Loved the zebra exercise"
SECRET_NAME = "Wilhelmina"


def _live_rig(client: TestClient, isolated_env: Path, *sections: dict[str, Any]) -> Iterator[tuple[TestClient, TestClient, str]]:
    """The main app live on a session with ``sections`` and a form last, the player app over its services, the session id."""
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "forms", isolated_env / "sessions.local.yaml")
    for section in sections:
        add_quiz_section(folder, section)
    add_quiz_section(folder, FORM_SECTION, at=99)
    app = client.app
    app.state.quiz.public_url = lambda: JOIN_BASE
    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    app.state.quiz_reach.last = Reach(OK, "reachable", time.time())  # after going live, which asks for a check
    player = create_player_app(app.state.quiz, app.state.polls, app.state.forms)
    with TestClient(player, client=("127.0.0.1", 50001)) as phone:
        yield client, phone, sid
    player.state.detach()


@pytest.fixture
def rig(client: TestClient, isolated_env: Path) -> Iterator[tuple[TestClient, TestClient, str]]:
    yield from _live_rig(client, isolated_env)


@pytest.fixture
def quiz_rig(client: TestClient, isolated_env: Path) -> Iterator[tuple[TestClient, TestClient, str]]:
    """The same, with the player quiz before the form."""
    yield from _live_rig(client, isolated_env, PLAYER_QUIZ)


def goto(client: TestClient, item_id: str) -> None:
    item = next(it for it in client.get("/api/live").json()["plan"]["run"]["items"] if it["id"] == item_id)
    assert client.post(f"/api/actions/goto/{item['index'] + 1}").status_code == 200


def token_of(client: TestClient) -> str:
    link = client.get("/api/forms/act-form/results").json()["link"]
    assert link and link.startswith("/play/form?t=")
    return link.split("t=", 1)[1]


def submit(phone: TestClient, token: str, answers: dict[str, Any], name: str = "", key: str | None = None,
           device: str = "phone-a") -> Any:
    return phone.post("/play/api/form", json={"t": token, "name": name, "answers": answers, "key": key, "phone": device})


def test_the_builder_form_with_every_type_saves_into_the_session(client: TestClient, isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "built", isolated_env / "sessions.local.yaml")
    session = client.get(f"/api/sessions/{sid}").json()["session"]
    session["sections"][0]["items"].append(FORM_SECTION["items"][0])
    assert client.put(f"/api/sessions/{sid}", json={"session": session}).status_code == 200
    saved = client.get(f"/api/sessions/{sid}").json()["session"]
    item = next(it for sec in saved["sections"] for it in sec["items"] if it["id"] == "act-form")
    qs = questions(item["options"])
    assert [q["type"] for q in qs] == ["text", "number", "scale", "stars", "nps", "choice"]
    assert qs[5]["choices"] == ["Talks", "Games", "Lunch"] and qs[5]["multiple"] is True
    assert (qs[2]["low"], qs[2]["high"], qs[2]["min"], qs[2]["max"]) == ("Not at all", "Very", 0, 10)
    assert qs[1]["unit"] == "h" and qs[0]["required"] and not qs[3]["required"]
    library = client.get("/api/forms/library").json()["forms"]
    assert any(f["session_id"] == sid and len(f["questions"]) == 6 for f in library)  # reusable in other sessions


def test_answers_are_checked_against_each_question() -> None:
    qs = questions({"questions": FORM_QUESTIONS})
    ok = clean_answers(qs, {"q1": "  keep \n the  games ", "q2": "2,5", "q3": 7, "q4": "4", "q5": 10, "q6": ["c", "A"]})
    assert ok == {"q1": "keep\nthe games", "q2": 2.5, "q3": 7, "q4": 4, "q5": 10, "q6": "A,C"}
    with pytest.raises(FormError) as bad:
        clean_answers(qs, {"q1": "", "q2": "99", "q3": "3.5", "q4": 6, "q6": ["D"]})
    assert set(bad.value.detail["errors"]) == {"q1", "q2", "q3", "q4", "q5", "q6"}
    with pytest.raises(FormError) as nothing:  # separators alone pick no choice, so they answer nothing
        clean_answers(qs, {"q1": "ok", "q2": 1, "q3": 7, "q4": 4, "q5": 10, "q6": " , "})
    assert nothing.value.detail["errors"] == {"q6": "pick one of the choices"}


def test_phones_submit_and_the_backstage_and_the_csv_add_them_up(rig) -> None:
    client, phone, sid = rig
    goto(client, "act-form")  # the first time on stage opens it
    token = token_of(client)
    first = {"q1": SECRET_TEXT, "q3": 8, "q4": 5, "q5": 10, "q6": ["A", "B"]}
    ack = submit(phone, token, first, name=SECRET_NAME, key="k1")
    assert ack.status_code == 200 and ack.json()["state"] == "accepted"
    assert submit(phone, token, first, name=SECRET_NAME, key="k1").json() == ack.json()  # a resend is stored once
    assert submit(phone, token, {"q1": "More breaks", "q2": "3", "q4": 3, "q5": 6, "q6": ["A"]},
                  key="k2", device="phone-b").status_code == 200  # anonymous
    assert submit(phone, token, {"q1": "Again: more games", "q5": 9}, name=SECRET_NAME, key="k3").status_code == 200
    missing = submit(phone, token, {"q3": 4}, key="k4")
    assert missing.status_code == 422 and set(missing.json()["error"]["detail"]["errors"]) == {"q1", "q5"}

    r = client.get("/api/forms/act-form/results").json()
    assert (r["count"], r["open"], r["url"]) == (3, True, f"{JOIN_BASE}/play/form?t={token}")
    by_id = {q["id"]: q for q in r["questions"]}
    assert (by_id["q5"]["score"], by_id["q5"]["promoters"], by_id["q5"]["detractors"]) == (33, 2, 1)
    assert by_id["q4"]["average"] == 4.0 and by_id["q3"]["average"] == 8.0
    assert [c["count"] for c in by_id["q6"]["choices"]] == [2, 1, 0]
    assert [(a["name"], a["text"]) for a in by_id["q1"]["answers"]] == [
        (SECRET_NAME, SECRET_TEXT), ("", "More breaks"), (SECRET_NAME, "Again: more games")]

    folder = client.app.state.live.folder
    rows = list(csv.reader(io.StringIO((folder / "live" / "captures" / "act-form.csv").read_text(encoding="utf-8-sig"))))
    assert rows[0][1:] == ["Name"] + [q["text"] for q in FORM_QUESTIONS]
    assert len(rows) == 4 and rows[1][2] == SECRET_TEXT and rows[1][7] == "Talks; Games" and rows[2][1] == ""
    assert client.get("/api/forms/act-form/csv").text.lstrip("﻿").count("\n") == 4
    # The responses survive a restart: forms.jsonl is replayed.
    assert (folder / "live" / FORMS_FILE).is_file()
    client.app.state.forms._session = None
    assert client.get("/api/forms/act-form/results").json()["count"] == 3


def until(ws: Any, pred: Any, seen: list[str], limit: int = 40) -> None:
    """Read ``ws`` into ``seen`` until a message matches ``pred``."""
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(json.dumps(m))
        if pred(m):
            return
    raise AssertionError("the message never arrived")


def test_the_stage_and_the_phones_never_receive_an_answer(rig) -> None:
    client, phone, _ = rig
    pin = client.get("/api/live").json()["state"]["room"]["pin"]
    member = phone.post("/play/api/join", json={"pin": pin, "nickname": ""}).json()
    form = client.app.state.live.item_by_id("act-form")
    form["options"] = {**form["options"], "show_count": True}  # the count may reach the stage: a push per response
    seen: list[str] = []
    with client.websocket_connect("/ws?role=stage") as stage, phone.websocket_connect("/play/ws") as phone_ws:
        phone_ws.send_json({"op": "hello", "player_id": member["player_id"], "secret": member["secret"]})
        goto(client, "act-form")
        until(stage, lambda m: m["type"] == "state" and (m["state"].get("join") or {}).get("item_id") == "act-form", seen)
        until(phone_ws, lambda m: (m.get("view") or {}).get("kind") == "form", seen)
        token = token_of(client)
        assert submit(phone, token, {"q1": SECRET_TEXT, "q5": 10}, name=SECRET_NAME).status_code == 200
        until(stage, lambda m: m["type"] == "state" and (m["state"].get("join") or {}).get("count") == 1, seen)
        view = phone.get(f"/play/api/state?player_id={member['player_id']}&secret={member['secret']}").json()["view"]
        assert view == {"kind": "form", "title": "Workshop feedback", "link": f"/play/form?t={token}"}
    state = client.get("/api/live").json()["state"]
    assert state["join"] == {"item_id": "act-form", "url": f"{JOIN_BASE}/play/form?t={token}", "ok": True,
                             "open": True, "count": 1}
    seen += [json.dumps(state), json.dumps(view), json.dumps(phone.get(f"/play/api/form?t={token}").json())]
    assert all(SECRET_TEXT not in s and SECRET_NAME not in s for s in seen)


def test_a_phone_that_played_the_quiz_sees_the_form_link(quiz_rig) -> None:
    """A form on stage comes before the phone's last quiz screen (its podium), or it could never open the form."""
    client, phone, _ = quiz_rig
    goto(client, "pq-lobby")
    pin = client.get("/api/live").json()["state"]["room"]["pin"]
    player = phone.post("/play/api/join", json={"pin": pin, "nickname": "Ana"}).json()
    assert player["state"] == "joined"
    goto(client, "act-form")
    view = phone.get(f"/play/api/state?player_id={player['player_id']}&secret={player['secret']}").json()["view"]
    assert view.get("kind") == "form", view
    assert view["link"] == f"/play/form?t={token_of(client)}"
    goto(client, "pq-podium")  # the quiz again: the phone is back in its game
    view = phone.get(f"/play/api/state?player_id={player['player_id']}&secret={player['secret']}").json()["view"]
    assert view.get("game_id") == "pq-lobby-1" and view.get("kind") != "form"


def test_the_chat_link_opens_the_form_and_a_closed_form_says_so(rig) -> None:
    client, phone, _ = rig
    goto(client, "act-form")
    token = token_of(client)
    page = phone.get(f"/play/form?t={token}")  # no PIN, no room join: the link alone
    assert page.status_code == 200 and "player.form" in page.text
    form = phone.get(f"/play/api/form?t={token}").json()
    assert form["state"] == "open" and [q["id"] for q in form["form"]["questions"]] == [q["id"] for q in FORM_QUESTIONS]
    assert "answers" not in json.dumps(form)
    assert client.post("/api/forms/act-form/close").json()["open"] is False
    closed = phone.get(f"/play/form?t={token}")
    assert closed.status_code == 410 and "This form is closed" in closed.text
    assert phone.get(f"/play/api/form?t={token}").json()["state"] == "closed"
    assert submit(phone, token, {"q1": "late", "q5": 3}).status_code == 410
    assert phone.get("/play/form?t=nope").status_code == 410
    # Reopened, the same link works again; closing the session closes it for good.
    assert client.post("/api/forms/act-form/open").json()["open"] is True
    assert phone.get(f"/play/api/form?t={token}").json()["state"] == "open"
    assert client.post("/api/live/deactivate").status_code == 200
    assert phone.get(f"/play/api/form?t={token}").json()["state"] == "closed"


def test_a_flood_from_one_phone_is_slowed_down(rig) -> None:
    client, phone, _ = rig
    goto(client, "act-form")
    token = token_of(client)
    codes = [submit(phone, token, {"q1": f"answer {n}", "q5": 8}, key=f"f{n}", device="same").status_code for n in range(10)]
    assert codes[:6] == [200] * 6 and 429 in codes
