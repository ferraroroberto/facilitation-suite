"""The public quiz-link check (#56): five distinct states, public DNS only, ``unknown`` never ``ok``.

No test leaves this PC: the network functions are passed in (or replaced by the
suite's ``isolated_env``), and the DNS parser is fed hand-built packets.
"""

from __future__ import annotations

import socket
import struct
import threading
from pathlib import Path
from typing import Any, Optional

import pytest

from src.quiz import reach
from src.quiz.reach import (
    FUNNEL_UNREACHABLE,
    LISTENER_DOWN,
    NOT_CONFIGURED,
    OK,
    STALE_S,
    UNKNOWN,
    DnsError,
    QuizReach,
    Reach,
    check,
)
from src.sessions import readiness

REAL_RESOLVE = reach.resolve_public  # the module's own (isolated_env replaces the attribute in every test)
URL = "https://quiz.example.test:10000"


def up(port: int) -> Optional[str]:
    return None


def down(port: int) -> Optional[str]:
    return "ConnectionRefusedError"


def resolves(*ips: str):  # noqa: ANN201 — a resolve stub
    return lambda host: list(ips)


def relay_ok(host: str, port: int, ip: str, path: str) -> Optional[str]:
    assert (host, port, path) == ("quiz.example.test", 10000, "/play/api/ping")
    return None


def relay_502(host: str, port: int, ip: str, path: str) -> Optional[str]:
    return "answered HTTP 502"


def run(url: str = URL, port: int = 8451, **kw: Any) -> Reach:
    kw.setdefault("local", up)
    kw.setdefault("resolve", resolves("198.51.100.7"))
    kw.setdefault("relay", relay_ok)
    return check(url, port, clock=lambda: 1000.0, **kw)


# ---- the five states ----

def test_ok_only_through_the_relay() -> None:
    r = run()
    assert (r.state, r.via) == (OK, "198.51.100.7") and "quiz.example.test:10000" in r.detail


def test_not_configured() -> None:
    assert run(url="").state == NOT_CONFIGURED
    assert run(port=0).state == NOT_CONFIGURED  # the listener is off
    assert run(url="http://quiz.example.test:10000").state == NOT_CONFIGURED
    assert run(url="not a url").state == NOT_CONFIGURED


def test_listener_down_is_checked_first() -> None:
    def never(*_: Any) -> Any:
        raise AssertionError("no public check while the listener is down")

    r = run(local=down, resolve=never, relay=never)
    assert r.state == LISTENER_DOWN and "127.0.0.1:8451" in r.detail


def test_funnel_unreachable() -> None:
    assert run(resolve=resolves()).state == FUNNEL_UNREACHABLE  # not in public DNS: Funnel off
    tried: list[str] = []

    def refused(host: str, port: int, ip: str, path: str) -> Optional[str]:
        tried.append(ip)
        return "ConnectionRefusedError"

    r = run(resolve=resolves("198.51.100.7", "198.51.100.8", "198.51.100.9"), relay=refused)
    assert r.state == FUNNEL_UNREACHABLE and tried == ["198.51.100.7", "198.51.100.8"]  # RELAY_TRIES addresses
    assert run(relay=relay_502).state == FUNNEL_UNREACHABLE


def test_a_second_relay_address_can_answer() -> None:
    r = run(resolve=resolves("198.51.100.7", "198.51.100.8"),
            relay=lambda h, p, ip, path: None if ip == "198.51.100.8" else "TimeoutError")
    assert (r.state, r.via) == (OK, "198.51.100.8")


def test_unknown_when_public_dns_does_not_answer_or_the_check_breaks() -> None:
    def silent(host: str) -> list[str]:
        raise DnsError("1.1.1.1: TimeoutError, 8.8.8.8: TimeoutError")

    assert run(resolve=silent).state == UNKNOWN

    def buggy(*_: Any) -> Any:
        raise KeyError("oops")

    assert run(relay=buggy).state == UNKNOWN  # a bug reads "unknown", never "ok"


def test_the_path_of_public_url_is_kept() -> None:
    seen: list[str] = []
    run(url="https://quiz.example.test/quiz/", relay=lambda h, p, ip, path: seen.append(f"{p}{path}"))
    assert seen == ["443/quiz/play/api/ping"]


def test_the_suite_never_resolves_for_real(isolated_env: Path) -> None:
    assert check(URL, 8451, local=up).state == UNKNOWN  # isolated_env replaced public DNS


# ---- public DNS: our own query, never this PC's resolver (MagicDNS) ----

def _answer(qid: int, rcode: int, records: list[tuple[int, bytes]]) -> bytes:
    _, query = reach.dns_query("quiz.example.test")
    question = query[12:]
    out = struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, len(records), 0, 0) + question
    for rtype, rdata in records:
        out += b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata
    return out


def test_dns_query_asks_for_an_a_record() -> None:
    qid, packet = reach.dns_query("quiz.example.test")
    assert struct.unpack(">H", packet[:2])[0] == qid
    assert packet[12:] == b"\x04quiz\x07example\x04test\x00" + struct.pack(">HH", 1, 1)


def test_parse_answer_reads_a_records_after_a_cname() -> None:
    cname = b"\x05relay\xc0\x0c"
    data = _answer(7, 0, [(5, cname), (1, socket.inet_aton("198.51.100.7")), (1, socket.inet_aton("198.51.100.8"))])
    assert reach.parse_answer(data, 7) == (0, ["198.51.100.7", "198.51.100.8"])
    assert reach.parse_answer(_answer(7, 3, []), 7) == (3, [])  # NXDOMAIN
    with pytest.raises(ValueError):
        reach.parse_answer(data, 8)  # another query's answer


def _fake_resolvers(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> list[str]:
    asked: list[str] = []

    def ask(host: str, server: str, timeout: float) -> tuple[int, list[str]]:
        asked.append(server)
        got = answers[server]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(reach, "_ask", ask)
    return asked


def test_resolve_public_moves_past_a_cached_nxdomain(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _fake_resolvers(monkeypatch, {"1.1.1.1": (3, []), "8.8.8.8": (0, ["198.51.100.7"])})
    assert REAL_RESOLVE("quiz.example.test") == ["198.51.100.7"] and asked == ["1.1.1.1", "8.8.8.8"]


def test_resolve_public_empty_or_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_resolvers(monkeypatch, {"1.1.1.1": (3, []), "8.8.8.8": TimeoutError()})
    assert REAL_RESOLVE("quiz.example.test") == []  # one resolver answered: it does not exist there
    _fake_resolvers(monkeypatch, {"1.1.1.1": TimeoutError(), "8.8.8.8": OSError("unreachable")})
    with pytest.raises(DnsError):
        REAL_RESOLVE("quiz.example.test")  # nobody answered: not "unreachable" but unknown


# ---- the service: last result, stale → unknown, one check at a time ----

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_current_is_unknown_before_the_first_check_and_once_stale() -> None:
    clock = FakeClock()
    pushes: list[int] = []
    svc = QuizReach(lambda: (URL, 8451), on_change=lambda: pushes.append(1), clock=clock,
                    run_check=lambda url, port: Reach(OK, "fine", clock()))
    assert svc.current()["state"] == UNKNOWN and not svc.fresh()
    assert svc.check_now()["state"] == OK and pushes == [1] and svc.fresh()
    clock.now += STALE_S + 1
    stale = svc.current()
    assert stale["state"] == UNKNOWN and stale["was"] == OK and not svc.fresh()  # never a stale "ok"


def test_a_check_arriving_mid_check_waits_for_it() -> None:
    gate, started, calls = threading.Event(), threading.Event(), []

    def slow(url: str, port: int) -> Reach:
        calls.append(1)
        started.set()
        gate.wait(5)
        return Reach(OK, "fine", 1000.0)

    svc = QuizReach(lambda: (URL, 8451), clock=lambda: 1000.0, run_check=slow)
    assert svc.kick() is True
    started.wait(5)
    assert svc.kick() is False  # one check at a time
    out: list[dict[str, Any]] = []
    t = threading.Thread(target=lambda: out.append(svc.check_now()))
    t.start()
    gate.set()
    t.join(5)
    assert calls == [1] and out[0]["state"] == OK


# ---- readiness: the row, only for quiz sessions ----

@pytest.mark.parametrize(("state", "row"), [(OK, "ok"), (LISTENER_DOWN, "warn"), (FUNNEL_UNREACHABLE, "warn"),
                                            (NOT_CONFIGURED, "todo"), (UNKNOWN, "unknown")])
def test_the_readiness_row_keeps_each_state(state: str, row: str) -> None:
    got = readiness.quiz_reach_check(Reach(state, "why").as_dict())
    assert (got["key"], got["state"], got["reach"], got["action"]) == ("quiz_reach", row, state, "check_quiz_reach")
    if state != OK:
        assert got["detail"].startswith(reach.LABELS[state].capitalize())
    assert readiness.quiz_reach_check(None)["state"] == "unknown"
    assert readiness.quiz_reach_check({"state": "weird", "detail": "?"})["state"] == "unknown"


def test_only_a_quiz_session_gets_the_row_and_the_api_checks_on_demand(client, isolated_env: Path) -> None:
    from tests.fixtures.demo import build_demo_session
    from tests.fixtures.quiz_plan import add_quiz_section

    ledger = isolated_env / "sessions.local.yaml"
    plain, _ = build_demo_session(isolated_env / "sessions" / "demo" / "plain", ledger)
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "quiz", ledger)
    add_quiz_section(folder)
    keys = lambda s: [c["key"] for c in client.get(f"/api/sessions/{s}").json()["readiness"]]  # noqa: E731
    assert "quiz_reach" not in keys(plain)
    row = next(c for c in client.get(f"/api/sessions/{sid}").json()["readiness"] if c["key"] == "quiz_reach")
    assert row["label"] == "Quiz public URL reachable" and row["action"] == "check_quiz_reach"
    assert row["reach"] in (UNKNOWN, NOT_CONFIGURED)  # not checked yet, or the kicked check already ran

    now = client.post("/api/quiz/reach").json()
    assert now["state"] == NOT_CONFIGURED  # the test config turns the player listener off
    assert client.get("/api/quiz/reach").json()["state"] == NOT_CONFIGURED
    row = next(c for c in client.get(f"/api/sessions/{sid}").json()["readiness"] if c["key"] == "quiz_reach")
    assert (row["state"], row["reach"]) == ("todo", NOT_CONFIGURED)

    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    items = client.get("/api/live").json()["plan"]["run"]["items"]
    lobby = next(n for n, it in enumerate(items) if it["id"] == "pq-lobby") + 1
    assert client.post(f"/api/actions/goto/{lobby}").status_code == 200
    assert client.get("/api/live").json()["state"]["quiz"]["reach"]["state"] == NOT_CONFIGURED  # the presenter chip
