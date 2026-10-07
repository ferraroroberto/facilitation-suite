"""The phone player API (#52): PIN join, reconnect-safe identity, acked idempotent answers,
per-IP rate limits, the push socket and the join QR — over the real player listener, which
shares the main app's loop and its one ``QuizService``."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.player import app as player_app
from app.player import ratelimit
from app.player.app import STATIC_DIR as PLAYER_STATIC
from app.player.app import create_player_app
from src.config import load_config
from src.jsonl import read_jsonl
from src.live.hub import LiveHub
from src.quiz.service import QUIZ_FILE, QuizService
from src.sessions.store import SessionStore
from tests.conftest import REPO_ROOT, write_test_config
from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_plan import add_quiz_section
from tests.test_quiz_engine import (  # noqa: F401 — fixtures
    Clock,
    at_lobby_with,
    clock,
    goto_id,
    make_rig,
    quiz_session,
    rig,
)

PUBLIC_URL = "https://quiz.example.test:10000"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_PORT_RACE_ATTEMPTS = 5  # the probed port and the listener's own bind are two separate sockets:
# between closing the probe and the listener binding, port churn elsewhere on the box can steal
# the number (#132). ``player.running`` tells us for certain whether the bind won.


class Phone:
    """One phone talking to the player listener over real HTTP."""

    def __init__(self, base: str, ip: str = "") -> None:
        self.base, self.ip = base, ip
        self.seen: list[Any] = []  # every JSON body the phone received

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.ip:
            req.add_header("X-Forwarded-For", self.ip)  # as cloudflared / Funnel send it, from loopback
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        out = json.loads(raw) if raw else None
        self.seen.append(out)
        return status, out

    def join(self, pin: str, nickname: str, key: str | None = None) -> dict[str, Any]:
        return self.call("POST", "/play/api/join", {"pin": pin, "nickname": nickname, "key": key})[1]

    def state(self, me: dict[str, Any]) -> dict[str, Any]:
        return self.call("GET", f"/play/api/state?player_id={me['player_id']}&secret={me['secret']}")[1]

    def answer(self, me: dict[str, Any], item_id: str, choice: int, elapsed_ms: int = 1000) -> dict[str, Any]:
        return self.call("POST", "/play/api/answer", {"player_id": me["player_id"], "secret": me["secret"],
                                                      "item_id": item_id, "choice": choice, "elapsed_ms": elapsed_ms})[1]


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def goto(main: TestClient, item_id: str) -> None:
    items = main.get("/api/live").json()["plan"]["run"]["items"]
    index = next(n for n, it in enumerate(items) if it["id"] == item_id)
    assert main.post(f"/api/actions/goto/{index + 1}").status_code == 200


def quiz_state(main: TestClient) -> dict[str, Any]:
    return main.get("/api/live").json()["state"]["quiz"]


@pytest.fixture
def game(isolated_env: Path) -> Iterator[tuple[TestClient, str]]:
    """The main app with its player listener on a free port, a quiz session live on its lobby."""
    from app.webapp.server import create_app

    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "play", isolated_env / "sessions.local.yaml")
    add_quiz_section(folder)

    for _attempt in range(_PORT_RACE_ATTEMPTS):
        port = _free_port()
        write_test_config(Path(os.environ["FS_CONFIG_PATH"]), session_root=str(isolated_env / "sessions"),
                          quiz={"public_port": port, "public_url": PUBLIC_URL})
        with TestClient(create_app(), client=("127.0.0.1", 50000)) as main:
            if not main.app.state.player.running:
                continue  # someone else grabbed the port between the probe and the bind — retry
            assert main.post("/api/live/activate", json={"session": sid}).status_code == 200
            goto(main, "pq-lobby")
            yield main, f"http://127.0.0.1:{port}"
            return
    pytest.fail(f"quiz player listener never bound after {_PORT_RACE_ATTEMPTS} attempts (port churn?)")


# ---- the game: join, answer, ack, reveal ----

def test_join_answer_ack_and_the_result(game) -> None:
    main, base = game
    phone, other = Phone(base), Phone(base)
    q = quiz_state(main)
    pin = q["pin"]
    assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"
    assert q["join_url"] == f"{PUBLIC_URL}/play?pin={pin}" and q["listener"] is True

    assert phone.join("000000" if pin != "000000" else "111111", "Ana")["state"] == "wrong_pin"
    ana = phone.join(pin, "Ana")
    assert ana["state"] == "joined" and ana["name"] == "Ana" and ana["view"]["view"]["phase"] == "lobby"
    ana2 = other.join(f"{pin[:3]} {pin[3:]}", " ana ")  # a spaced PIN; the same nickname gets a suffix
    assert ana2["state"] == "joined" and ana2["name"] == "ana (2)"
    retried = other.join(pin, "whoever", key="k-1")
    assert other.join(pin, "whoever", key="k-1")["player_id"] == retried["player_id"]  # a retried join: same player
    assert phone.call("POST", "/play/api/join", {"pin": pin, "nickname": "   "})[0] == 422

    goto(main, "pq-1")
    view = phone.state(ana)
    assert view["state"] == "ok" and view["view"]["phase"] == "question" and view["view"]["open"] is True
    assert view["view"]["tiles"] == [1, 2, 3, 4] and view["view"]["answer"] is None
    assert view["view"]["deadline_ms"] > view["now_ms"]
    # The texts the stage shows, so a phone can play without watching it (#90) — in tile order.
    assert view["view"]["question"] == "How many sides has a triangle?"
    assert view["view"]["answers"] == ["Two", "Three", "Four", "Five"]
    assert other.state(retried)["view"]["answers"] == ["Two", "Three", "Four", "Five"]  # a phone that never answers

    first = phone.answer(ana, "pq-1", 2, elapsed_ms=250)
    assert first["state"] == "accepted" and first["choice"] == 2
    assert phone.answer(ana, "pq-1", 2, elapsed_ms=900)["state"] == "accepted"  # a retry gets the same ack
    assert phone.answer(ana, "pq-1", 3)["state"] == "duplicate"  # the first answer stands
    assert other.answer(ana2, "pq-1", 1)["state"] == "accepted"
    mine = phone.state(ana)["view"]["answer"]
    assert mine["choice"] == 2 and mine["elapsed_ms"] <= 250  # clamped to how long the server has been asking
    # Nothing the phones got so far says which answer is right.
    assert "correct" not in _keys(phone.seen) | _keys(other.seen)
    assert phone.call("POST", "/play/api/answer", {**{k: ana[k] for k in ("player_id", "secret")},
                                                   "item_id": "pq-1", "choice": 5})[0] == 422

    assert main.post("/api/actions/next").status_code == 200  # reveal
    v = phone.state(ana)["view"]
    assert v["phase"] == "reveal" and v["answer"]["correct"] is True and v["correct"] == [2]
    assert (v["score"], v["rank"], v["last_points"]) == (1000, 1, 1000)  # counted once despite the retry
    lost = other.state(ana2)["view"]
    assert lost["answer"]["correct"] is False and lost["score"] == 0 and lost["correct"] == [2]
    assert other.state(retried)["view"]["correct"] == [2]  # no answer: the phone still learns the right one
    assert other.answer(retried, "pq-1", 2)["state"] == "too_late"  # locked: a distinct state, not dropped

    main.post("/api/actions/next")  # leaderboard
    goto(main, "pq-2")  # two answers: two tiles, two texts, and again no correct answer while it is open
    two = phone.state(ana)["view"]
    assert (two["question"], two["tiles"], two["answers"]) == ("Is a square a rectangle?", [1, 2], ["Yes", "No"])
    assert "correct" not in _keys(two)

    for body in phone.seen + other.seen:  # never the plan's options or other players' answers
        assert "options" not in _keys(body) and "distribution" not in _keys(body) and "players" not in _keys(body)


def test_resume_kick_and_a_wrong_secret(game) -> None:
    main, base = game
    phone = Phone(base)
    pin = quiz_state(main)["pin"]
    ana = phone.join(pin, "Ana")
    ids = {"player_id": ana["player_id"], "secret": ana["secret"]}
    back = phone.call("POST", "/play/api/resume", ids)[1]
    assert back["state"] == "resumed" and back["name"] == "Ana" and back["view"]["view"]["player_id"] == ana["player_id"]

    wrong = {"player_id": ana["player_id"], "secret": "not-it"}
    assert phone.call("POST", "/play/api/resume", wrong)[1]["state"] == "unknown_player"
    assert phone.state(wrong)["state"] == "unknown_player" and phone.state(wrong)["view"] is None
    goto(main, "pq-1")
    assert phone.answer(wrong, "pq-1", 2)["state"] == "unknown_player"  # the secret is required: no trusted path

    assert main.post(f"/api/actions/quiz_kick/{ana['player_id']}").status_code == 200
    assert phone.call("POST", "/play/api/resume", ids)[1]["state"] == "kicked"
    assert phone.state(ana)["view"]["kicked"] is True
    assert phone.answer(ana, "pq-1", 2)["state"] == "kicked"

    assert main.post("/api/live/deactivate").status_code == 200  # no session live: "no quiz now", identity kept
    idle = phone.state(ana)
    assert (idle["state"], idle["view"]) == ("no_game", None)


def test_an_answer_while_no_session_is_live_is_retried_not_refused(game) -> None:
    """After a restart the session is not live until the presenter goes live again (#56):
    an answer retried in that window must not say ``unknown_player`` — the phone would
    drop its identity — but ``no_game``, and the same answer is accepted once it is live."""
    main, base = game
    phone = Phone(base)
    pin = quiz_state(main)["pin"]
    ana = phone.join(pin, "Ana")
    sid = main.get("/api/live").json()["plan"]["session"]["id"]
    goto(main, "pq-1")
    assert main.post("/api/live/deactivate").status_code == 200  # the window between a restart and "Go live"

    assert phone.answer(ana, "pq-1", 2)["state"] == "no_game"
    assert phone.call("POST", "/play/api/resume", {k: ana[k] for k in ("player_id", "secret")})[1]["state"] == "no_game"

    assert main.post("/api/live/activate", json={"session": sid}).status_code == 200
    assert quiz_state(main)["phase"] == "question"  # still open: its deadline has not passed
    assert phone.answer(ana, "pq-1", 2)["state"] == "accepted"


def test_the_phone_speaks_the_session_language(game) -> None:
    """#91: the ping says the live session's language (the join screen, before a view), and every
    view carries it as ``lang``; changing the session's language changes both."""
    main, base = game
    phone = Phone(base)
    assert phone.call("GET", "/play/api/ping")[1]["lang"] == "en"
    ana = phone.join(quiz_state(main)["pin"], "Ana")
    assert ana["view"]["view"]["lang"] == "en"

    sid = main.get("/api/live").json()["plan"]["session"]["id"]
    session = main.get(f"/api/sessions/{sid}").json()["session"]
    assert main.put(f"/api/sessions/{sid}", json={"session": {**session, "language": "es"}}).status_code == 200
    deadline = time.monotonic() + 5
    while main.get("/api/live").json()["plan"]["run"].get("language") != "es":  # the live plan reloads on the loop
        assert time.monotonic() < deadline, "the live plan never reloaded"
        time.sleep(0.05)
    assert phone.call("GET", "/play/api/ping")[1]["lang"] == "es"
    assert phone.state(ana)["view"]["lang"] == "es"
    back = phone.call("POST", "/play/api/resume", {k: ana[k] for k in ("player_id", "secret")})[1]
    assert back["view"]["view"]["lang"] == "es"


def test_the_socket_pushes_each_phone_its_own_view(game) -> None:
    main, base = game
    phone = Phone(base)
    pin = quiz_state(main)["pin"]
    ana = phone.join(pin, "Ana")
    ws_url = base.replace("http://", "ws://") + "/play/ws"
    with connect(ws_url, open_timeout=5) as ws:
        ws.send(json.dumps({"op": "ping", "t": 7}))
        assert json.loads(ws.recv(timeout=5))["type"] == "pong"
        ws.send(json.dumps({"op": "hello", "player_id": ana["player_id"], "secret": ana["secret"]}))
        first = json.loads(ws.recv(timeout=5))
        assert first["type"] == "view" and first["state"] == "ok" and first["view"]["phase"] == "lobby"

        goto(main, "pq-1")
        pushed = json.loads(ws.recv(timeout=5))
        assert pushed["view"]["phase"] == "question" and pushed["view"]["item_id"] == "pq-1"
        assert phone.answer(ana, "pq-1", 2)["state"] == "accepted"
        mine = json.loads(ws.recv(timeout=5))
        assert mine["view"]["answer"]["choice"] == 2 and "correct" not in _keys(mine)
        main.post("/api/actions/next")
        revealed = json.loads(ws.recv(timeout=5))
        assert revealed["view"]["phase"] == "reveal" and revealed["view"]["answer"]["correct"] is True
    # The socket is gone: answers still ack over HTTP and polling still sees the game.
    assert _eventually_empty(main)
    main.post("/api/actions/next")  # leaderboard
    assert phone.state(ana)["view"]["phase"] == "leaderboard"


def _eventually_empty(main: TestClient) -> bool:
    deadline = time.time() + 5
    while time.time() < deadline:
        if not main.app.state.player.app.state.push.conns:
            return True
        time.sleep(0.05)
    return False


def test_a_socket_that_never_says_hello_is_closed(game) -> None:
    _, base = game
    with connect(base.replace("http://", "ws://") + "/play/ws", open_timeout=5) as ws:
        ws.send(json.dumps({"op": "answer", "choice": 1}))  # not a hello: refused
        with pytest.raises(Exception):  # noqa: B017 — the server closes it
            ws.recv(timeout=5)


def test_the_hello_deadline_holds_after_a_ping(game, monkeypatch) -> None:
    _, base = game
    monkeypatch.setattr(player_app, "HELLO_TIMEOUT_S", 0.5)
    with connect(base.replace("http://", "ws://") + "/play/ws", open_timeout=5) as ws:
        ws.send(json.dumps({"op": "ping", "t": 1}))
        assert json.loads(ws.recv(timeout=5))["type"] == "pong"  # a ping is answered before the hello…
        with pytest.raises(ConnectionClosed):  # …but the socket still has to say hello in time
            ws.recv(timeout=5)


# ---- rate limits (per client IP, as the tunnel reports it) ----

def test_rate_limits_are_per_forwarded_ip(game, monkeypatch) -> None:
    main, base = game
    monkeypatch.setattr(ratelimit, "JOIN", ratelimit.Limit(burst=4, per_s=0.001))
    monkeypatch.setattr(ratelimit, "WRONG_PIN", ratelimit.Limit(burst=2, per_s=0.001))
    pin = quiz_state(main)["pin"]
    nat, home = Phone(base, "203.0.113.7"), Phone(base, "198.51.100.9")
    for n in range(4):
        assert nat.join(pin, f"p{n}")["state"] == "joined"
    status, body = nat.call("POST", "/play/api/join", {"pin": pin, "nickname": "p5"})
    assert status == 429 and body["error"]["code"] == "slow_down" and body["error"]["detail"]["retry_after_s"] > 0
    assert home.join(pin, "elsewhere")["state"] == "joined"  # another address has its own bucket
    wrong = "123456" if pin != "123456" else "654321"
    assert home.join(wrong, "x")["state"] == "wrong_pin"
    assert home.join(wrong, "x")["state"] == "wrong_pin"
    status, body = home.call("POST", "/play/api/join", {"pin": pin, "nickname": "y"})
    assert status == 429 and body["error"]["code"] == "slow_down"  # PIN guessing stops (its join bucket has room)


def test_through_cloudflare_a_spoofed_forwarded_for_is_still_keyed_on_the_real_ip(game, monkeypatch) -> None:
    # Cloudflare APPENDS the connecting IP to whatever X-Forwarded-For the phone sent (probed through
    # the tunnel, #82): "6.6.6.N,<real>". Each request spoofing a new address must still share the
    # real address's bucket — the rightmost entry — or a PIN sweep could rotate past the limit.
    main, base = game
    monkeypatch.setattr(ratelimit, "JOIN", ratelimit.Limit(burst=3, per_s=0.001))
    pin = quiz_state(main)["pin"]
    for n in range(3):
        assert Phone(base, f"6.6.6.{n},203.0.113.7").join(pin, f"p{n}")["state"] == "joined"
    status, body = Phone(base, "6.6.6.99,203.0.113.7").call("POST", "/play/api/join", {"pin": pin, "nickname": "p9"})
    assert status == 429 and body["error"]["code"] == "slow_down"
    assert Phone(base, "6.6.6.99,198.51.100.9").join(pin, "other")["state"] == "joined"  # another real address


def test_the_default_limits_let_60_players_behind_one_nat_play() -> None:
    now = [0.0]
    lim = ratelimit.RateLimiter(clock=lambda: now[0])
    ip = "203.0.113.7"
    assert all(lim.allow("join", ip, ratelimit.JOIN) == 0 for _ in range(120))  # 60 joins + 60 reloads at once
    for _question in range(20):  # 60 answers per question, each sent three times, 10 s apart
        assert all(lim.allow("answer", ip, ratelimit.ANSWER) == 0 for _ in range(180))
        now[0] += 10
    assert lim.allow("x", ip, ratelimit.Limit(burst=1, per_s=2)) == 0
    assert lim.allow("x", ip, ratelimit.Limit(burst=1, per_s=2)) == pytest.approx(0.5)


# ---- the PIN (engine + service) ----

def test_every_game_has_its_own_pin_and_it_survives_a_restart(quiz_session, clock: Clock) -> None:  # noqa: F811
    sid, folder = quiz_session
    hub, quiz = make_rig(sid, clock)
    goto_id(hub, "qz-lobby")
    first = quiz.game_on_stage()
    assert len(first.pin) == 6 and hub.snapshot()["state"]["quiz"]["pin"] == first.pin
    assert quiz.join_pin(first.pin, "Ana").state == "joined"
    assert quiz.join_pin("999999" if first.pin != "999999" else "111111", "Bo").state == "wrong_pin"
    quiz.new_game()
    second = quiz.game_on_stage()
    assert second.game_id != first.game_id and second.pin and second.pin != first.pin
    assert quiz.join_pin(first.pin, "Cy").state == "no_game"  # an old game's PIN: not the game on stage
    recs = [r for r in read_jsonl(folder / "live" / QUIZ_FILE) if r["op"] == "pin"]
    assert [r["pin"] for r in recs] == [first.pin, second.pin]

    _, quiz2 = make_rig(sid, clock)  # a fresh server on the same folder
    assert {g.game_id: g.pin for g in quiz2.games.values()} == {first.game_id: first.pin, second.game_id: second.pin}


def test_a_game_from_before_pins_gets_one(quiz_session, clock: Clock) -> None:  # noqa: F811
    sid, folder = quiz_session
    hub, quiz = make_rig(sid, clock)
    goto_id(hub, "qz-lobby")
    path = folder / "live" / QUIZ_FILE
    path.write_text("".join(json.dumps(r) + "\n" for r in read_jsonl(path) if r["op"] != "pin"), encoding="utf-8")
    hub2, quiz2 = make_rig(sid, clock)
    pin = quiz2.game_on_stage().pin
    assert len(pin) == 6 and [r["pin"] for r in read_jsonl(path) if r["op"] == "pin"] == [pin]


def test_change_listeners_hear_every_record(rig) -> None:  # noqa: F811
    hub, quiz = rig
    heard: list[int] = []
    quiz.change_listeners.append(lambda: heard.append(1))
    quiz.change_listeners.insert(0, lambda: 1 / 0)  # a broken listener never stops the game
    a, = at_lobby_with(hub, quiz, "Ana")
    goto_id(hub, "qz-1")
    n = len(heard)
    assert n >= 2
    assert quiz.answer(a.player_id, a.secret, "qz-1", 2).state == "accepted"
    assert len(heard) == n + 1


# ---- the join QR on the main app ----

def test_the_join_qr(game) -> None:
    main, _ = game
    pin = quiz_state(main)["pin"]
    r = main.get("/api/quiz/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-quiz-qr"] == "ok" and f'aria-label="{PUBLIC_URL}/play?pin={pin}"' in r.text
    assert main.get(f"/api/quiz/qr.svg?pin={pin}").headers["x-quiz-qr"] == "ok"
    assert main.get("/api/quiz/qr.svg?pin=000001").status_code == 404
    # Behind RemoteAuth like every /api route: another device needs the token.
    stranger = TestClient(main.app, client=("100.64.0.50", 5000))
    assert stranger.get("/api/quiz/qr.svg").status_code == 401
    assert main.post("/api/settings/remote/token").status_code == 200
    token = main.app.state.config.remote.token
    paired = TestClient(main.app, client=("100.64.0.50", 5000), headers={"Authorization": f"Bearer {token}"})
    assert paired.get("/api/quiz/qr.svg").status_code == 200

    goto(main, "pq-podium")
    main.app.state.quiz.public_url = lambda: ""  # not configured: a distinct placeholder and state
    r = main.get("/api/quiz/qr.svg")
    assert r.status_code == 200 and r.headers["x-quiz-qr"] == "not-configured" and "not configured" in r.text
    assert quiz_state(main)["join_url"] is None


def test_no_qr_off_a_quiz(client) -> None:
    r = client.get("/api/quiz/qr.svg")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_game"


# ---- the page and its own static files ----

def test_the_page_and_its_fleet_copies(game) -> None:
    _, base = game
    phone = Phone(base)
    with urllib.request.urlopen(base + "/play?pin=123456", timeout=5) as r:
        html = r.read().decode("utf-8")
    assert "<title>Quiz</title>" in html and "/play/static/play.js" in html
    assert not re.search(r'(?:href|src)="/(?!play/)', html)  # nothing from the main app, not even its /static
    for name in ("play.js", "play.css", "fleet/tokens.css", "fleet/button.css"):
        with urllib.request.urlopen(f"{base}/play/static/{name}", timeout=5) as r:
            assert r.status == 200 and r.headers["Cache-Control"] == "no-cache", name
    assert phone.call("GET", "/play/static/../server.py")[0] == 404


def test_the_player_copies_of_the_fleet_css_match_the_app() -> None:
    main_static = REPO_ROOT / "app" / "webapp" / "static"
    pairs = {"tokens.css": main_static / "css" / "tokens.css", "base.css": main_static / "_vendored" / "base" / "base.css",
             "card.css": main_static / "_vendored" / "card" / "card.css",
             "button.css": main_static / "_vendored" / "button" / "button.css"}
    for name, source in pairs.items():
        assert (PLAYER_STATIC / "fleet" / name).read_bytes() == source.read_bytes(), (
            f"app/player/static/fleet/{name} drifted — copy {source.relative_to(REPO_ROOT)} over it")


def test_the_player_code_never_calls_the_trusted_answer() -> None:
    for path in (REPO_ROOT / "app" / "player").rglob("*.py"):
        code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
        assert "answer_trusted(" not in code, path


def test_a_player_app_on_its_own_service_reaches_that_service(isolated_env: Path) -> None:
    """The listener is handed the main app's service: the player app never builds its own."""
    quiz = QuizService(LiveHub(SessionStore(load_config())))
    app = create_player_app(quiz)
    assert app.state.push.notify in quiz.change_listeners
    app.state.detach()
    assert app.state.push.notify not in quiz.change_listeners


def test_player_requests_stay_out_of_the_access_log(game) -> None:
    """The polling URL carries the secret, and 60 phones poll every second: the listener's request
    lines are dropped, while the shared access logger keeps working for the main app."""
    import logging

    from app.player.listener import QuietPlayerRequests

    lg = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, QuietPlayerRequests) for f in lg.filters)

    def line(path: str) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                 ("1.2.3.4:5", "GET", path, "1.1", 200), None)

    assert not lg.filter(line("/play/api/state?player_id=p1&secret=s3cret"))
    assert lg.filter(line("/api/live"))
