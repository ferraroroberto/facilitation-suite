"""Is the public quiz link reachable? (#56, #82) — the readiness item and the presenter chip.

One check, six distinct states (``unknown`` is never folded into ``ok``):

- ``not_configured`` — ``quiz.public_url`` is empty (or not an ``https://`` URL),
  or the player listener is off (``quiz.public_port = 0``).
- ``listener_down`` — the player listener does not answer
  ``http://127.0.0.1:<public_port>/play/api/ping`` on this PC.
- ``tunnel_down`` — ``quiz.public_url``'s host is one the Cloudflare tunnel
  config (``webapp/cloudflared.yml``) publishes, and cloudflared is not running
  or holds no edge connection (its ``/ready`` on ``127.0.0.1:8452``; ``src/tunnel.py``).
- ``public_unreachable`` — public DNS does not know the host (the DNS record is
  missing or not propagated yet), or the public edge it points at does not
  answer the ping (refused, timed out, a TLS error, a non-200 such as the
  502 Cloudflare and the Funnel relay both give when nothing serves the host).
- ``unknown`` — the check could not establish it: never run, stale, public DNS
  did not answer at all (this PC offline?), or an unexpected error.
- ``ok`` — the ping answered ``{"ok": true}`` **through the public edge**.

The edge (Cloudflare's, or Tailscale Funnel's relay in the fallback set-up) is
reached the way a phone on mobile data reaches it: the host is resolved through
public DNS (``1.1.1.1``, then ``8.8.8.8``, queried directly over UDP — never
this PC's resolver, which may answer differently, e.g. MagicDNS with the
tailnet address), then an HTTPS request goes to that address with the real host
name for SNI and certificate checks. A proxied Cloudflare name answers public
DNS with A records directly; a CNAME chain in an answer is followed to its A
records all the same.

The check blocks for up to a few seconds, so it never runs on the event loop:
``QuizReach.check_now`` runs in a thread (``asyncio.to_thread``, a readiness
request's worker thread, or ``kick``), and every reader gets the last result
(``current``), which turns ``unknown`` once it is older than ``STALE_S``.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import secrets
import socket
import ssl
import struct
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import src.tunnel as src_tunnel

logger = logging.getLogger(__name__)

OK = "ok"
LISTENER_DOWN = "listener_down"
TUNNEL_DOWN = "tunnel_down"
PUBLIC_UNREACHABLE = "public_unreachable"
NOT_CONFIGURED = "not_configured"
UNKNOWN = "unknown"
STATES = (OK, LISTENER_DOWN, TUNNEL_DOWN, PUBLIC_UNREACHABLE, NOT_CONFIGURED, UNKNOWN)
LABELS = {OK: "reachable", LISTENER_DOWN: "listener down", TUNNEL_DOWN: "tunnel down",
          PUBLIC_UNREACHABLE: "public URL unreachable", NOT_CONFIGURED: "not configured", UNKNOWN: "unknown"}

PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")
PING_PATH = "/play/api/ping"
LOCAL_TIMEOUT_S = 2.0
DNS_TIMEOUT_S = 2.0
EDGE_TIMEOUT_S = 5.0
EDGE_TRIES = 2  # edge addresses tried before calling it unreachable
INTERVAL_S = 120.0  # the periodic check while a live session has quiz items
STALE_S = 300.0  # an older result is "unknown", never a stale "ok"


class DnsError(Exception):
    """No public resolver answered (the reason says which failed how)."""


@dataclass
class Reach:
    state: str
    detail: str
    checked_at: Optional[float] = None  # epoch seconds
    via: Optional[str] = None  # the edge address that answered
    ms: Optional[int] = None  # the edge round trip

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "label": LABELS[self.state], "detail": self.detail,
                "checked_at": self.checked_at, "via": self.via, "ms": self.ms}


# ------------------------------------------------------------ public DNS


def _encode_name(host: str) -> bytes:
    out = b""
    for label in host.rstrip(".").split("."):
        raw = label.encode("idna")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad host name {host!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def dns_query(host: str) -> tuple[int, bytes]:
    """A DNS query for ``host``'s A records: (its id, the packet)."""
    qid = secrets.randbits(16)
    return qid, struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _encode_name(host) + struct.pack(">HH", 1, 1)


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        n = data[pos]
        if n == 0:
            return pos + 1
        if n & 0xC0 == 0xC0:  # a compression pointer ends the name
            return pos + 2
        pos += 1 + n


def parse_answer(data: bytes, qid: int) -> tuple[int, list[str]]:
    """(rcode, the A records' addresses) of a DNS response; ``ValueError`` if it is not ours."""
    if len(data) < 12:
        raise ValueError("short DNS response")
    rid, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if rid != qid or not flags & 0x8000:
        raise ValueError("not the answer to this query")
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(data, pos) + 4
    ips: list[str] = []
    for _ in range(ancount):
        pos = _skip_name(data, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:  # CNAMEs on the way are skipped; their A records follow
            ips.append(socket.inet_ntoa(data[pos:pos + 4]))
        pos += rdlen
    return flags & 0x000F, ips


def resolve_public(host: str, resolvers: tuple[str, ...] = PUBLIC_RESOLVERS,
                   timeout: float = DNS_TIMEOUT_S) -> list[str]:
    """``host``'s addresses as public DNS has them; ``[]`` when no resolver that answered knows it.

    Asks each resolver in turn and returns the first one's addresses (a
    resolver can still serve a cached NXDOMAIN minutes after a new name went
    live — seen in #49 — so an empty answer moves on to the next); raises
    ``DnsError`` only when none of them answered at all.
    """
    failures, answered = [], False
    for server in resolvers:
        try:
            rcode, ips = _ask(host, server, timeout)
        except (OSError, ValueError) as exc:
            failures.append(f"{server}: {type(exc).__name__}")
            continue
        if rcode not in (0, 3):  # 3 = NXDOMAIN, a real answer
            failures.append(f"{server}: rcode {rcode}")
            continue
        answered = True
        if ips:
            return ips
    if answered:
        return []
    raise DnsError(", ".join(failures))


def _ask(host: str, server: str, timeout: float) -> tuple[int, list[str]]:
    qid, packet = dns_query(host)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(packet, (server, 53))
        deadline = time.monotonic() + timeout
        while True:  # skip a stray datagram that is not the answer to this query
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            data, _ = sock.recvfrom(4096)
            try:
                return parse_answer(data, qid)
            except ValueError:
                if time.monotonic() >= deadline:
                    raise


# ------------------------------------------------------------- requests


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to ``ip`` with ``host`` for the Host header, SNI and the certificate check (``curl --resolve``)."""

    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _is_pong(status: int, body: bytes) -> bool:
    if status != 200:
        return False
    try:
        return json.loads(body).get("ok") is True
    except (ValueError, AttributeError):
        return False


def ping_local(port: int, timeout: float = LOCAL_TIMEOUT_S) -> Optional[str]:
    """``None`` when the player listener answers its ping on this PC, else why not."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{PING_PATH}", timeout=timeout) as r:
            return None if _is_pong(r.status, r.read()) else f"answered HTTP {r.status}"
    except OSError as exc:  # URLError, HTTPError, timeouts and refusals are all OSErrors
        return f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"


def ping_edge(host: str, port: int, ip: str, path: str, timeout: float = EDGE_TIMEOUT_S) -> Optional[str]:
    """``None`` when ``https://host:port<path>`` answers the ping via ``ip``, else why not."""
    conn = _PinnedHTTPSConnection(host, ip, port, timeout)
    try:
        conn.request("GET", path, headers={"Cache-Control": "no-cache"})
        r = conn.getresponse()
        return None if _is_pong(r.status, r.read()) else f"answered HTTP {r.status}"
    except (OSError, http.client.HTTPException) as exc:  # ssl.SSLError and timeouts are OSErrors
        return f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


# ---------------------------------------------------------------- check


def check(public_url: str, public_port: int, *,
          local: Optional[Callable[[int], Optional[str]]] = None,
          tunnel: Optional[Callable[[str], Optional[str]]] = None,
          resolve: Optional[Callable[[str], list[str]]] = None,
          edge: Optional[Callable[[str, int, str, str], Optional[str]]] = None,
          clock: Callable[[], float] = time.time) -> Reach:
    """The public link's state now (blocking — run it in a thread; see the module docstring).

    ``local``, ``tunnel``, ``resolve`` and ``edge`` default to this module's
    ``ping_local``, ``src.tunnel.problem``, ``resolve_public`` and ``ping_edge``,
    looked up at call time (the test suite replaces the network ones so no test
    leaves this PC).
    """
    local, resolve, edge = local or ping_local, resolve or resolve_public, edge or ping_edge
    tunnel = tunnel or src_tunnel.problem
    now = clock()
    url = (public_url or "").strip()
    if not public_port:
        return Reach(NOT_CONFIGURED, "The player listener is off (quiz.public_port = 0)", now)
    if not url:
        return Reach(NOT_CONFIGURED, "quiz.public_url is empty — set it in the config", now)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return Reach(NOT_CONFIGURED, f"quiz.public_url {url!r} is not an https:// address", now)
    try:
        host, port = parts.hostname, parts.port or 443
        path = parts.path.rstrip("/") + PING_PATH
        why = local(public_port)
        if why is not None:
            return Reach(LISTENER_DOWN, f"127.0.0.1:{public_port} does not answer ({why})", now)
        why = tunnel(host)
        if why is not None:
            return Reach(TUNNEL_DOWN, f"{why} — restart the tray (tray.bat --restart); its log: cloudflared.log", now)
        try:
            ips = resolve(host)
        except DnsError as exc:
            return Reach(UNKNOWN, f"Public DNS did not answer ({exc}) — is this PC online?", now)
        if not ips:
            return Reach(PUBLIC_UNREACHABLE,
                         f"{host} is not in public DNS — is its DNS record in place (or still propagating)?", now)
        errors = []
        for ip in ips[:EDGE_TRIES]:
            started = time.monotonic()
            why = edge(host, port, ip, path)
            if why is None:
                ms = int((time.monotonic() - started) * 1000)
                return Reach(OK, f"Players can reach {host}:{port} (via {ip}, {ms} ms)", now, ip, ms)
            errors.append(f"{ip}: {why}")
        return Reach(PUBLIC_UNREACHABLE, f"The public edge does not answer the ping ({'; '.join(errors)})", now)
    except Exception as exc:  # noqa: BLE001 — a bug in the check must read "unknown", never "ok"
        logger.exception("❌ quiz reach: the check failed")
        return Reach(UNKNOWN, f"The check failed ({type(exc).__name__}: {exc})", now)


class QuizReach:
    """The last result, the periodic check and the on-demand one (see the module docstring)."""

    def __init__(self, settings: Callable[[], tuple[str, int]], *, wanted: Callable[[], bool] = lambda: False,
                 on_change: Callable[[], None] = lambda: None, clock: Callable[[], float] = time.time,
                 run_check: Callable[..., Reach] = check) -> None:
        self.settings = settings  # () → (quiz.public_url, quiz.public_port)
        self.wanted = wanted  # () → a live session has quiz items (the periodic check runs then)
        self.on_change = on_change  # after every check (called from the checking thread)
        self.clock = clock
        self.run_check = run_check
        self.last: Optional[Reach] = None
        self._lock = threading.Lock()

    def current(self) -> dict[str, Any]:
        """The last result, or ``unknown`` when there is none or it is older than ``STALE_S``."""
        last = self.last
        if last is None:
            return Reach(UNKNOWN, "Not checked yet").as_dict()
        age = self.clock() - (last.checked_at or 0)
        if age > STALE_S:
            stale = Reach(UNKNOWN, f"Last checked {int(age // 60)} min ago — check again", last.checked_at)
            return {**stale.as_dict(), "was": last.state}
        return last.as_dict()

    def fresh(self) -> bool:
        return self.last is not None and self.clock() - (self.last.checked_at or 0) <= STALE_S

    def check_now(self) -> dict[str, Any]:
        """Run the check (blocking, never on the event loop); a caller arriving mid-check gets that check's result."""
        if not self._lock.acquire(blocking=False):
            with self._lock:
                return self.current()
        try:
            url, port = self.settings()
            result = self.run_check(url, port)
            changed = self.last is None or self.last.state != result.state  # logged once per change
            self.last = result
        finally:
            self._lock.release()
        if changed:
            log = logger.info if result.state == OK else logger.warning
            log("%s quiz public link: %s — %s", "✅" if result.state == OK else "⚠️", LABELS[result.state], result.detail)
        try:
            self.on_change()
        except Exception:  # noqa: BLE001 — a view push failing must not break the check
            logger.exception("❌ quiz reach: pushing the result failed")
        return self.current()

    def kick(self) -> bool:
        """Start a check in a background thread unless one is running; ``True`` when it started one."""
        if self._lock.locked():
            return False
        threading.Thread(target=self.check_now, name="quiz-reach", daemon=True).start()
        return True

    async def run_periodic(self, interval: float = INTERVAL_S) -> None:
        """Every ``interval`` while ``wanted()``: the check, in a worker thread (the lifespan runs this)."""
        while True:
            if self.wanted():
                await asyncio.to_thread(self.check_now)
            await asyncio.sleep(interval)
