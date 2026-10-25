"""The public quiz-link check (#56, #82): six distinct states, public DNS only, ``unknown`` never ``ok``.

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

from src import tunnel
from src.quiz import reach
from src.quiz.reach import (
    LISTENER_DOWN,
    NOT_CONFIGURED,
    OK,
    PUBLIC_UNREACHABLE,
    STALE_S,
    TUNNEL_DOWN,
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


def edge_ok(host: str, port: int, ip: str, path: str) -> Optional[str]:
    assert (host, port, path) == ("quiz.example.test", 10000, "/play/api/ping")
    return None


def edge_502(host: str, port: int, ip: str, path: str) -> Optional[str]:
    return "answered HTTP 502"


def no_tunnel(host: str) -> Optional[str]:
    return None


def run(url: str = URL, port: int = 8451, **kw: Any) -> Reach:
    kw.setdefault("local", up)
    kw.setdefault("tunnel", no_tunnel)
    kw.setdefault("resolve", resolves("198.51.100.7"))
    kw.setdefault("edge", edge_ok)
    return check(url, port, clock=lambda: 1000.0, **kw)


# ---- the six states ----

def test_ok_only_through_the_edge() -> None:
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

    r = run(local=down, tunnel=never, resolve=never, edge=never)
    assert r.state == LISTENER_DOWN and "127.0.0.1:8451" in r.detail


def test_tunnel_down_before_any_public_check() -> None:
    def never(*_: Any) -> Any:
        raise AssertionError("no public check while the tunnel is down")

    seen: list[str] = []

    def stopped(host: str) -> Optional[str]:
        seen.append(host)
        return "cloudflared is not running (127.0.0.1:8452 does not answer: URLError)"

    r = run(tunnel=stopped, resolve=never, edge=never)
    assert r.state == TUNNEL_DOWN and seen == ["quiz.example.test"] and "cloudflared is not running" in r.detail
    assert reach.LABELS[TUNNEL_DOWN] == "tunnel down"


def test_public_unreachable() -> None:
    assert run(resolve=resolves()).state == PUBLIC_UNREACHABLE  # not in public DNS: no record (yet)
    tried: list[str] = []

    def refused(host: str, port: int, ip: str, path: str) -> Optional[str]:
        tried.append(ip)
        return "ConnectionRefusedError"

    r = run(resolve=resolves("198.51.100.7", "198.51.100.8", "198.51.100.9"), edge=refused)
    assert r.state == PUBLIC_UNREACHABLE and tried == ["198.51.100.7", "198.51.100.8"]  # EDGE_TRIES addresses
    assert run(edge=edge_502).state == PUBLIC_UNREACHABLE
    assert reach.LABELS[PUBLIC_UNREACHABLE] == "public URL unreachable"


def test_a_second_edge_address_can_answer() -> None:
    r = run(resolve=resolves("198.51.100.7", "198.51.100.8"),
            edge=lambda h, p, ip, path: None if ip == "198.51.100.8" else "TimeoutError")
    assert (r.state, r.via) == (OK, "198.51.100.8")


def test_unknown_when_public_dns_does_not_answer_or_the_check_breaks() -> None:
    def silent(host: str) -> list[str]:
        raise DnsError("1.1.1.1: TimeoutError, 8.8.8.8: TimeoutError")

    assert run(resolve=silent).state == UNKNOWN

    def buggy(*_: Any) -> Any:
        raise KeyError("oops")

    assert run(edge=buggy).state == UNKNOWN  # a bug reads "unknown", never "ok"
    assert run(tunnel=buggy).state == UNKNOWN


def test_the_path_of_public_url_is_kept() -> None:
    seen: list[str] = []
    run(url="https://quiz.example.test/quiz/", edge=lambda h, p, ip, path: seen.append(f"{p}{path}"))
    assert seen == ["443/quiz/play/api/ping"]


def test_the_suite_never_resolves_for_real(isolated_env: Path) -> None:
    assert check(URL, 8451, local=up).state == UNKNOWN  # isolated_env replaced public DNS
    assert tunnel.hostnames() == set()  # …and points the tunnel config at an empty temp dir


# ---- the tunnel: which hosts are ours, and cloudflared's own readiness ----

def test_the_tunnel_publishes_the_hosts_of_its_config(isolated_env: Path) -> None:
    cfg = isolated_env / "cloudflared.yml"
    assert tunnel.problem("quiz.example.test") is None  # no config: not ours to check
    cfg.write_text("tunnel: synthetic\ningress:\n  - hostname: Quiz.Example.Test\n    service: http://127.0.0.1:8451\n"
                   "  - service: http_status:404\n", encoding="utf-8")
    assert tunnel.hostnames() == {"quiz.example.test"}
    assert tunnel.problem("other.example.test") is None  # another ingress (e.g. Funnel): skipped
    cfg.write_text("ingress: [unclosed", encoding="utf-8")
    assert tunnel.hostnames() == set()  # unreadable: logged, treated as no tunnel


def test_cloudflared_readiness_states(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import http.server

    (isolated_env / "cloudflared.yml").write_text("ingress:\n  - hostname: quiz.example.test\n    service: x\n",
                                                  encoding="utf-8")
    with socket.socket() as s:  # a port nobody listens on: cloudflared is not running
        s.bind(("127.0.0.1", 0))
        monkeypatch.setattr(tunnel, "METRICS_PORT", s.getsockname()[1])
    assert "not running" in (tunnel.problem("quiz.example.test") or "")

    reply: list[tuple[int, bytes]] = []

    class Metrics(http.server.BaseHTTPRequestHandler):  # cloudflared's /ready, as the probe saw it
        def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
            status, body = reply[-1]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Metrics)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(tunnel, "METRICS_PORT", srv.server_address[1])
    try:
        reply.append((503, b'{"status":503,"readyConnections":0}'))
        assert "no connection to Cloudflare" in (tunnel.problem("quiz.example.test") or "")
        reply.append((200, b'{"status":200,"readyConnections":0}'))
        assert "no connection to Cloudflare" in (tunnel.problem("quiz.example.test") or "")
        reply.append((200, b'{"status":200,"readyConnections":2}'))
        assert tunnel.problem("quiz.example.test") is None
        assert tunnel.problem("QUIZ.example.test") is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_tray_runs_cloudflared_on_this_config_with_fixed_metrics(tmp_path: Path) -> None:
    t = tunnel.Tunnel(tmp_path / "cloudflared.yml", log_file=tmp_path / "cloudflared.log")
    cmd = t.command("cloudflared")
    assert cmd[:4] == ["cloudflared", "tunnel", "--config", str(tmp_path / "cloudflared.yml")] and cmd[-1] == "run"
    assert cmd[cmd.index("--metrics") + 1] == f"127.0.0.1:{tunnel.METRICS_PORT}"
    assert cmd[cmd.index("--logfile") + 1] == str(tmp_path / "cloudflared.log")
    assert t.start() is False and not t.running()  # no config: logged, nothing spawned
    t.stop()
    assert t.start() is False  # stopped: never again


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
    cname = b"\x04edge\xc0\x0c"
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

@pytest.mark.parametrize(("state", "row"), [(OK, "ok"), (LISTENER_DOWN, "warn"), (TUNNEL_DOWN, "warn"),
                                            (PUBLIC_UNREACHABLE, "warn"), (NOT_CONFIGURED, "todo"),
                                            (UNKNOWN, "unknown")])
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
