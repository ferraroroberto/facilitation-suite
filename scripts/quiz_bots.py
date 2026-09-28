"""Quiz load bots (#56): N phones play a whole game against a DISPOSABLE instance.

The script is the rehearsal harness for the quiz's reliability promises (#34 §4):
no lost joins, no lost or double-scored answers, and a restart mid-question
that loses nothing. It never touches the live app or a real session folder:

1. It builds a synthetic session (the demo deck + a generated quiz section) in
   a temp folder and boots its own server on it (the e2e suite's
   ``boot_instance``: temp config, ledger, data dir and an empty ``.env``).
2. It is the **host**: it takes the session live, goes to the lobby, reads the
   PIN and then steps every phase through the main app's
   ``POST /api/actions/next`` (the button the presenter, the remote and the
   Stream Deck send), waiting on each question until every bot has answered.
3. Each bot is a phone: it joins with the PIN and a nickname through the
   **player API** (``/play/api/join``; the same idempotency ``key`` on every
   retry), follows the game over ``/play/ws`` or by polling
   ``/play/api/state``, answers after a random delay with a random tile and
   retries the same answer until it is acked (``play.js``'s rules: transport
   errors, 5xx and 429 are retried — 429 after its ``retry_after_s``). Some bots
   randomly "reload" mid-game: they drop the socket and the HTTP connection,
   keep only their stored identity, ``resume`` and may switch WebSocket ↔
   polling.
4. At the end it checks the engine's own record, the session's
   ``live/quiz.jsonl`` (replayed with ``src.quiz.service.replay``): every bot
   joined exactly once, every accepted answer is recorded once and was acked
   (by the answer's reply, or by the view after a reload), no bot is missing an
   answer, the podium's scores equal both the replay and the script's own
   scoring of the acks (``src.quiz.engine.points``), and there were no
   unexpected errors. It prints join / question-push / answer-ack latency
   percentiles.

``--restart-mid-question`` kills the server process (a crash, no clean
shutdown) once half the bots have answered question ``--restart-at``, starts
it again on the same ports and, ``--reactivate-after`` seconds later, takes the
session live again as the presenter's "Go live" would. It then checks the game
came back as it was (game, PIN, question, deadline, players, answers so far)
and that the next leaderboard equals the one before plus that question's
points, per player.

**Through the public relay.** Give the disposable instance a fixed player port
and publish it on a *temporary* Funnel path, then point the bots at it and
resolve the host through public DNS (as a phone on mobile data does; this PC's
own resolver answers with the tailnet address and would skip the relay)::

    tailscale funnel --bg --https=10000 --set-path /fs-bots56 http://127.0.0.1:18461/play
    .venv/Scripts/python.exe scripts/quiz_bots.py --player-port 18461 \\
        --player-base https://<host>.ts.net:10000/fs-bots56 --public-dns
    tailscale funnel --https=10000 --set-path /fs-bots56 off

All bots then share this PC's public IP, which is the corporate-NAT case the
player API's per-IP rate limits (``app/player/ratelimit.py``) are sized for.

Usage (from the repo root)::

    .venv/Scripts/python.exe scripts/quiz_bots.py [--bots 60] [--questions 10] [--seed 7]
        [--drop 0.15] [--poll-share 0.3] [--restart-mid-question [--restart-at 5]]

Exit code 0 when every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx2
import websockets
from websockets.asyncio.client import connect as ws_connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.activities.registry import options_with_defaults  # noqa: E402 — needs the sys.path line above
from src.jsonl import read_jsonl  # noqa: E402
from src.quiz.engine import points  # noqa: E402
from src.quiz.model import QuizQuestion, question_from_item  # noqa: E402
from src.quiz.reach import resolve_public  # noqa: E402
from src.quiz.service import QUESTION, QUIZ_FILE, replay  # noqa: E402
from tests.e2e.conftest import boot_instance, stop_instance  # noqa: E402
from tests.fixtures.demo import build_demo_session  # noqa: E402
from tests.fixtures.quiz_plan import add_quiz_section  # noqa: E402

logger = logging.getLogger("quiz_bots")

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
LIVE_PORTS = (8449, 8451)  # the live app and its player listener: never a bot target
TIME_LIMIT = 30  # seconds per question (the restart question gets RESTART_TIME_LIMIT)
RESTART_TIME_LIMIT = 90
MAX_THINK_S = 8.0  # a bot answers within this many seconds of seeing the question
PAUSE_S = 0.8  # the host's pause on the reveal and the leaderboard
POLL_S = 1.0  # play.js polls once a second
HTTP_TIMEOUT_S = 10.0
EXPECTED_ANSWER_STATES = {"accepted"}
TRANSIENT_STATES = {"no_game", "not_open"}  # a restart window / a race with the question opening: retry


# ------------------------------------------------------------------ the quiz


def bots_quiz(count: int, rng: random.Random, restart_at: Optional[int]) -> dict[str, Any]:
    """A synthetic quiz section: lobby, ``count`` sums with four answers, podium."""
    items: list[dict[str, Any]] = [{"kind": "activity", "id": "bq-lobby", "type": "quiz_lobby",
                                    "options": {"title": "Bots"}}]
    for n in range(1, count + 1):
        a, b = rng.randint(2, 40), rng.randint(2, 40)
        right = rng.randint(1, 4)
        answers = {f"answer_{k}": str(a + b + (k - right) * rng.randint(1, 3)) for k in range(1, 5)}
        items.append({"kind": "activity", "id": f"bq-{n}", "type": "quiz", "question": f"What is {a} + {b}?",
                      "options": {**answers, "correct": str(right),
                                  "time_limit": RESTART_TIME_LIMIT if n == restart_at else TIME_LIMIT,
                                  "points": "double" if n == count else "standard"}})
    items.append({"kind": "activity", "id": "bq-podium", "type": "quiz_podium"})
    return {"id": "sec-bots", "name": "Bots quiz", "minutes": 10, "items": items}


def questions_of(section: dict[str, Any]) -> dict[str, QuizQuestion]:
    return {it["id"]: question_from_item({**it, "options": options_with_defaults(QUESTION, it["options"])})
            for it in section["items"] if it["type"] == QUESTION}


# --------------------------------------------------------------------- stats


@dataclass
class Stats:
    latency: dict[str, list[float]] = field(default_factory=lambda: {"join": [], "push": [], "ack": []})
    counts: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def error(self, text: str) -> None:
        self.errors.append(text)
        logger.warning("⚠️ %s", text)


def percentiles(values: list[float]) -> str:
    if not values:
        return "n=0"
    xs = sorted(values)

    def at(p: float) -> float:  # nearest rank
        return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))] * 1000

    return f"n={len(xs):4d}  p50={at(50):6.0f} ms  p90={at(90):6.0f} ms  p99={at(99):6.0f} ms  max={xs[-1] * 1000:6.0f} ms"


# ----------------------------------------------------------------------- bot


class Bot:
    """One phone (see the module docstring)."""

    def __init__(self, n: int, base: str, pin: str, stats: Stats, rng: random.Random, opened: dict[str, float],
                 *, drop: float, poll_share: float) -> None:
        self.name, self.base, self.pin, self.stats, self.rng = f"Bot {n:02d}", base.rstrip("/"), pin, stats, rng
        self.opened = opened  # item id → when the host opened it (the push latency)
        self.drop, self.poll_share = drop, poll_share
        self.key = uuid.uuid4().hex  # the join's idempotency key (the page keeps one per join attempt)
        self.transport = "poll" if rng.random() < poll_share else "ws"
        self.player_id: Optional[str] = None
        self.secret: Optional[str] = None
        self.joined_ids: set[str] = set()
        self.acks: dict[str, tuple[int, int]] = {}  # item id → (choice, elapsed_ms) the server acked
        self.ack_via: dict[str, str] = {}  # item id → "reply" | "view"
        self.intent: dict[str, int] = {}  # item id → the tile this bot means (a reload taps it again)
        self.seen: set[str] = set()
        self.shown_at: dict[str, float] = {}  # item id → when the tiles showed since the last reload
        self.drops_planned: set[str] = set()
        self.drops = self.switches = 0
        self.answering: Optional[asyncio.Task[None]] = None
        self.transport_task: Optional[asyncio.Task[None]] = None
        self.reloads: set[asyncio.Task[None]] = set()
        self.reloading = False
        self.client = self._client()
        self.done = asyncio.Event()  # saw the podium

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=HTTP_TIMEOUT_S, headers={"Content-Type": "application/json"})

    # ---------------------------------------------------------- requests

    async def _request(self, kind: str, method: str, path: str, body: Any = None) -> Optional[dict[str, Any]]:
        """One call retried like the page does; the JSON reply, or ``None`` after a non-retryable status."""
        for attempt in range(1000):
            try:
                r = await self.client.request(method, self.base + path, json=body)
            except httpx2.HTTPError as exc:
                self.stats.counts[f"retry {kind}: {type(exc).__name__}"] += 1
                await asyncio.sleep(min(3.0, 0.3 * 2 ** attempt))
                continue
            if r.status_code == 429:
                self.stats.counts[f"429 {kind}"] += 1
                wait = ((r.json().get("error") or {}).get("detail") or {}).get("retry_after_s") or 1
                await asyncio.sleep(float(wait))
                continue
            if r.status_code >= 500:
                self.stats.counts[f"retry {kind}: HTTP {r.status_code}"] += 1
                await asyncio.sleep(min(3.0, 0.3 * 2 ** attempt))
                continue
            if r.status_code != 200:
                self.stats.error(f"{self.name}: {kind} → HTTP {r.status_code} {r.text[:120]}")
                return None
            return r.json()
        self.stats.error(f"{self.name}: {kind} never got through")
        return None

    async def join(self) -> None:
        started = time.monotonic()
        body = {"pin": self.pin, "nickname": self.name, "key": self.key}
        while True:
            out = await self._request("join", "POST", "/api/join", body)
            if out is None:
                return
            if out["state"] == "joined":
                self.stats.latency["join"].append(time.monotonic() - started)
                self.player_id, self.secret = out["player_id"], out["secret"]
                self.joined_ids.add(out["player_id"])
                if out.get("view"):
                    self.on_view(out["view"])
                return
            self.stats.error(f"{self.name}: join → {out['state']}")
            return

    async def resume(self) -> None:
        while True:
            out = await self._request("resume", "POST", "/api/resume", self.ids())
            if out is None:
                return
            if out["state"] == "resumed":
                if out.get("view"):
                    self.on_view(out["view"])
                return
            if out["state"] == "no_game":  # the server restarted and is not live yet: keep the identity, retry
                self.stats.counts["resume: no_game (retried)"] += 1
                await asyncio.sleep(1.0)
                continue
            self.stats.error(f"{self.name}: resume → {out['state']}")
            return

    def ids(self) -> dict[str, str]:
        return {"player_id": self.player_id or "", "secret": self.secret or ""}

    # -------------------------------------------------------------- views

    def on_view(self, msg: dict[str, Any]) -> None:
        state = msg.get("state")
        if state == "no_game":
            self.stats.counts["view: no_game"] += 1
            return
        if state != "ok":
            self.stats.error(f"{self.name}: view → {state}")
            return
        v = msg["view"]
        if v.get("kicked"):
            self.stats.error(f"{self.name}: kicked")
            return
        item, phase = v.get("item_id"), v.get("phase")
        if phase == "podium":
            self.done.set()
            return
        if not item or not isinstance(v.get("tiles"), list):
            return
        if item not in self.seen and phase == "question":
            self.seen.add(item)
            if item in self.opened:
                self.stats.latency["push"].append(time.monotonic() - self.opened[item])
            if self.rng.random() < self.drop:
                self.drops_planned.add(item)
                asyncio.get_running_loop().call_later(self.rng.uniform(0.2, MAX_THINK_S), self._drop_soon, item)
        mine = v.get("answer")
        if mine and item not in self.acks:  # accepted, but the reply was lost (a reload): the view acks it
            self.acks[item] = (int(mine["choice"]), int(mine["elapsed_ms"]))
            self.ack_via[item] = "view"
        if phase == "question" and v.get("open") and not mine and item not in self.acks:
            self.shown_at.setdefault(item, time.monotonic())
            if self.answering is None or self.answering.done():
                left_s = (int(v["deadline_ms"]) - int(msg.get("now_ms") or v["deadline_ms"])) / 1000
                self.answering = asyncio.create_task(self.answer(item, v["tiles"], left_s))

    async def answer(self, item: str, tiles: list[int], left_s: float) -> None:
        choice = self.intent.setdefault(item, self.rng.choice(tiles))
        await asyncio.sleep(self.rng.uniform(0.3, max(0.5, min(MAX_THINK_S, left_s - 4))))
        elapsed = int((time.monotonic() - self.shown_at.get(item, time.monotonic())) * 1000)
        body = {**self.ids(), "item_id": item, "choice": choice, "elapsed_ms": elapsed}
        started = time.monotonic()
        while item not in self.acks:
            out = await self._request("answer", "POST", "/api/answer", body)
            if out is None:
                return
            state = out["state"]
            if state in EXPECTED_ANSWER_STATES:
                self.stats.latency["ack"].append(time.monotonic() - started)
                self.acks[item] = (int(out["choice"]), int(out["elapsed_ms"]))
                self.ack_via.setdefault(item, "reply")
                return
            if state in TRANSIENT_STATES:
                self.stats.counts[f"answer: {state} (retried)"] += 1
                await asyncio.sleep(1.0)
                continue
            self.stats.error(f"{self.name}: answer {item} → {state}")
            return

    # ---------------------------------------------------------- transport

    async def poll_once(self) -> None:
        try:
            r = await self.client.get(self.base + "/api/state", params=self.ids())
        except httpx2.HTTPError as exc:
            self.stats.counts[f"retry poll: {type(exc).__name__}"] += 1
            return
        if r.status_code == 200:
            self.on_view(r.json())
        else:
            self.stats.counts[f"retry poll: HTTP {r.status_code}"] += 1

    async def run_transport(self) -> None:
        if self.transport == "poll":
            while True:
                await self.poll_once()
                await asyncio.sleep(POLL_S)
        url = self.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
        backoff = 0.5
        while True:
            try:
                async with ws_connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"op": "hello", **self.ids()}))
                    backoff = 0.5
                    async for raw in ws:
                        self.on_view(json.loads(raw))
                self.stats.counts["ws: closed by the server"] += 1
            except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
                self.stats.counts[f"ws: {type(exc).__name__}"] += 1
            until = time.monotonic() + backoff  # the socket is down: poll while it comes back
            while time.monotonic() < until:
                await self.poll_once()
                await asyncio.sleep(POLL_S)
            backoff = min(5.0, backoff * 2)

    def _drop_soon(self, item: str) -> None:
        task = asyncio.create_task(self.reload(item))
        self.reloads.add(task)
        task.add_done_callback(self.reloads.discard)

    async def reload(self, item: str) -> None:
        """The phone reloads: socket, connection and in-flight answer gone; only the stored identity stays."""
        if self.done.is_set() or self.player_id is None or self.reloading:
            return
        self.reloading = True
        try:
            await self._reload()
        finally:
            self.reloading = False

    async def _reload(self) -> None:
        self.drops += 1
        for task in (self.transport_task, self.answering):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        self.answering = None
        await self.client.aclose()
        self.shown_at.clear()
        await asyncio.sleep(self.rng.uniform(0.3, 2.0))
        self.client = self._client()
        if self.rng.random() < 0.5:
            self.transport = "poll" if self.transport == "ws" else "ws"
            self.switches += 1
        await self.resume()
        self.transport_task = asyncio.create_task(self.run_transport())

    async def run(self, start_delay: float) -> None:
        await asyncio.sleep(start_delay)
        await self.join()
        if self.player_id is None:
            return
        self.transport_task = asyncio.create_task(self.run_transport())
        await self.done.wait()

    async def close(self) -> None:
        for task in (*self.reloads, self.transport_task, self.answering):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        await self.client.aclose()


# ---------------------------------------------------------------------- host


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class Disposable:
    """The disposable server: a temp root, fixed ports, start / kill / stop."""

    def __init__(self, root: Path, port: int, player_port: int) -> None:
        self.root, self.port, self.player_port = root, port, player_port
        self.proc: Any = None
        self.base = ""

    def start(self) -> None:
        self.proc, inst = boot_instance(self.root, port=self.port, player_port=self.player_port)
        self.base = inst.base_url
        self.port = int(self.base.rsplit(":", 1)[1])
        self.player_port = int(inst.player_url.rsplit(":", 1)[1])

    def kill(self) -> None:
        """A crash: the whole process tree killed at once — no shutdown, no lifespan, nothing flushed.

        ``.venv/Scripts/python.exe`` is a launcher with the real interpreter as its child, so
        the tree is killed and the ports waited on until that child is gone too.
        """
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True,
                       creationflags=NO_WINDOW, check=False)
        self.proc.wait(timeout=10)
        self.proc.fs_log.close()
        deadline = time.monotonic() + 15
        log = self.root / "server.log"
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError):  # held until the interpreter is gone; kept for the report
                if log.is_file():
                    log.replace(self.root / f"server-{int(time.time())}.log")
            if not log.is_file() and not any(_listening(p) for p in (self.port, self.player_port)):
                return
            time.sleep(0.2)
        raise RuntimeError("the killed server still holds its log or its ports after 15 s")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            stop_instance(self.proc)


class Host:
    """The facilitator: the main app's REST API on loopback (the presenter's and Stream Deck's calls)."""

    def __init__(self, server: Disposable, sid: str) -> None:
        self.server, self.sid = server, sid
        self.client = httpx2.AsyncClient(timeout=HTTP_TIMEOUT_S)

    async def call(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        r = await self.client.request(method, self.server.base + path, json=body)
        r.raise_for_status()
        return r.json()

    async def quiz(self) -> dict[str, Any]:
        return (await self.call("GET", "/api/live"))["state"]["quiz"]

    async def next(self) -> None:
        await self.call("POST", "/api/actions/next")

    async def go_live(self) -> None:
        await self.call("POST", "/api/live/activate", {"session": self.sid})

    async def goto(self, item_id: str) -> None:
        items = (await self.call("GET", "/api/live"))["plan"]["run"]["items"]
        index = next(n for n, it in enumerate(items) if it["id"] == item_id)
        await self.call("POST", f"/api/actions/goto/{index + 1}")

    async def step_to(self, phase: str) -> dict[str, Any]:
        """``next`` until the quiz shows ``phase`` (a question may already have revealed itself at time up)."""
        for _ in range(3):
            q = await self.quiz()
            if q["phase"] == phase:
                return q
            await self.next()
            await asyncio.sleep(0.1)
        raise RuntimeError(f"the quiz did not reach {phase} (it shows {q['phase']})")


# ---------------------------------------------------------------------- run


def pin_to_public_dns(base: str) -> str:
    """Resolve ``base``'s host through public DNS and make this process connect there (``curl --resolve``)."""
    host = urlsplit(base).hostname or ""
    try:
        local = sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, socket.AF_INET)})
    except OSError as exc:
        local = [f"({exc})"]
    ips = resolve_public(host)
    if not ips:
        raise SystemExit(f"❌ {host} is not in public DNS — is the Funnel on?")
    real = socket.getaddrinfo

    def pinned(h: Any, *args: Any, **kwargs: Any) -> Any:
        name = h.decode() if isinstance(h, bytes) else h  # anyio (httpx2) passes the IDNA-encoded bytes
        return real(ips[0] if name == host else h, *args, **kwargs)

    socket.getaddrinfo = pinned
    logger.info("ℹ️ %s: this PC's resolver says %s; public DNS says %s → the bots connect to %s (the relay)",
                host, ", ".join(local), ", ".join(ips), ips[0])
    return ips[0]


async def peer_of(base: str) -> str:
    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_S) as c:
        r = await c.get(base.rstrip("/") + "/api/ping")
        stream = r.extensions.get("network_stream")
        addr = stream.get_extra_info("server_addr") if stream is not None else None
        return f"HTTP {r.status_code} from {addr[0] if addr else '?'}"


async def play(args: argparse.Namespace, server: Disposable, sid: str, section: dict[str, Any],
               stats: Stats, rng: random.Random) -> dict[str, Any]:
    """Run the game; returns what the checks need."""
    host = Host(server, sid)
    try:
        return await _play(args, server, host, section, stats, rng)
    finally:
        await host.client.aclose()


async def _play(args: argparse.Namespace, server: Disposable, host: Host, section: dict[str, Any],
                stats: Stats, rng: random.Random) -> dict[str, Any]:
    questions = questions_of(section)
    order = [it["id"] for it in section["items"] if it["type"] == QUESTION]
    await host.go_live()
    await host.goto("bq-lobby")
    lobby = await host.quiz()
    pin, game_id = lobby["pin"], lobby["game_id"]
    base = args.player_base or f"http://127.0.0.1:{server.player_port}/play"
    logger.info("ℹ️ game %s · PIN %s · %d bots → %s", game_id, pin, args.bots, base)
    if args.public_dns:
        logger.info("ℹ️ ping through the relay: %s", await peer_of(base))

    opened: dict[str, float] = {}
    bots = [Bot(n, base, pin, stats, rng, opened, drop=args.drop, poll_share=args.poll_share)
            for n in range(1, args.bots + 1)]
    tasks = [asyncio.create_task(b.run(rng.uniform(0, args.join_spread))) for b in bots]
    started = time.monotonic()
    while True:
        q = await host.quiz()
        if q["player_count"] >= args.bots and all(b.player_id for b in bots):
            break
        if time.monotonic() - started > args.join_spread + 60 or all(t.done() for t in tasks):
            stats.error(f"only {q['player_count']} of {args.bots} joined")
            break
        await asyncio.sleep(0.25)
    logger.info("✅ %d players in the lobby after %.1f s", q["player_count"], time.monotonic() - started)

    boards: dict[str, dict[str, int]] = {}
    restart: dict[str, Any] = {}
    for k, item in enumerate(order, start=1):
        opened[item] = time.monotonic()
        await host.next()
        q = await host.quiz()
        if (q["phase"], q["item_id"]) != ("question", item):
            raise RuntimeError(f"expected question {item}, the quiz shows {q['phase']} on {q['item_id']}")
        deadline = time.monotonic() + questions[item].time_limit + 3
        while time.monotonic() < deadline:
            q = await host.quiz()
            if q["phase"] != "question":
                break  # time up: the server revealed it
            if args.restart_mid_question and k == args.restart_at and not restart and q["answered_count"] >= args.bots // 2:
                restart = await restart_mid_question(server, host, bots, item, q, stats, args)
                continue
            if q["answered_count"] >= args.bots and all(item in b.acks for b in bots):
                break
            await asyncio.sleep(0.25)
        answered = q["answered_count"]
        await host.step_to("reveal")
        await asyncio.sleep(PAUSE_S)
        q = await host.step_to("leaderboard")
        boards[item] = {e["id"]: e["score"] for e in q["leaderboard"]}
        logger.info("ℹ️ question %d/%d: %d answered · leader %s", k, len(order), answered,
                    q["leaderboard"][0]["name"] if q["leaderboard"] else "-")
        if restart and restart.get("item") == item:
            restart["board_after"] = boards[item]
            restart["board_before"] = boards.get(order[k - 2], {}) if k > 1 else {}
        await asyncio.sleep(PAUSE_S)
    await host.next()
    q = await host.quiz()
    if q["phase"] != "podium":
        raise RuntimeError(f"expected the podium, the quiz shows {q['phase']}")
    podium = {e["id"]: e["score"] for e in q["leaderboard"]}
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.gather(*(b.done.wait() for b in bots)), 30)
    for b in bots:
        await b.close()
    for t in tasks:
        t.cancel()
    return {"bots": bots, "game_id": game_id, "podium": podium, "questions": questions, "order": order,
            "restart": restart}


async def restart_mid_question(server: Disposable, host: Host, bots: list[Bot], item: str, before: dict[str, Any],
                               stats: Stats, args: argparse.Namespace) -> dict[str, Any]:
    """Kill the server mid-question, start it again, go live again; check the game came back as it was."""
    acked = sum(1 for b in bots if item in b.acks)
    logger.info("💥 killing the server mid-question %s (%d answered, %d acked, %d s left)", item,
                before["answered_count"], acked, (before["deadline_ms"] - int(time.time() * 1000)) // 1000)
    killed = time.monotonic()
    server.kill()
    await asyncio.sleep(1.0)
    await asyncio.to_thread(server.start)
    up = time.monotonic() - killed
    logger.info("ℹ️ server back after %.1f s; going live again in %.0f s (the presenter's \"Go live\")", up,
                args.reactivate_after)
    await asyncio.sleep(args.reactivate_after)
    await host.go_live()
    after = await host.quiz()
    same = {k: (before.get(k), after.get(k)) for k in ("game_id", "pin", "item_id", "phase", "deadline_ms", "player_count")}
    for key, (was, now) in same.items():
        if was != now:
            stats.error(f"restart: {key} was {was!r}, is {now!r}")
    if after["answered_count"] < before["answered_count"]:
        stats.error(f"restart: {before['answered_count']} answers before, {after['answered_count']} after")
    if not after.get("listener"):
        stats.error("restart: the player listener did not come back (port busy?)")
    logger.info("✅ after the restart: %s", ", ".join(f"{k}={v[1]}" for k, v in same.items()) +
                f", answered={after['answered_count']} (was {before['answered_count']}), listener={after.get('listener')}")
    return {"item": item, "down_s": time.monotonic() - killed, "before": before, "after": after}


# -------------------------------------------------------------------- checks


def check(result: dict[str, Any], folder: Path, stats: Stats) -> list[str]:
    """Every check (see the module docstring); returns the failures."""
    bots: list[Bot] = result["bots"]
    questions: dict[str, QuizQuestion] = result["questions"]
    order: list[str] = result["order"]
    fails: list[str] = []
    records = read_jsonl(folder / "live" / QUIZ_FILE)
    game = replay(records)[result["game_id"]]
    mine = [r for r in records if r.get("game") == result["game_id"]]

    joins = [r for r in mine if r["op"] == "join"]
    keys = Counter(r.get("key") for r in joins)
    if len(joins) != len(bots):
        fails.append(f"{len(joins)} join records for {len(bots)} bots")
    for b in bots:
        if keys[b.key] != 1 or len(b.joined_ids) != 1:
            fails.append(f"{b.name}: joined {keys[b.key]} time(s) ({len(b.joined_ids)} ids)")
    if len(game.active()) != len(bots):
        fails.append(f"{len(game.active())} active players for {len(bots)} bots")

    answer_recs = Counter((r["player_id"], r["item_id"]) for r in mine if r["op"] == "answer")
    doubles = {k: n for k, n in answer_recs.items() if n > 1}
    if doubles:
        fails.append(f"{len(doubles)} answers recorded more than once: {list(doubles)[:3]}")
    server = {k: (a.choice, a.elapsed_ms) for k, a in game.answers.items()}
    acked = {(b.player_id, item): ack for b in bots for item, ack in b.acks.items()}
    lost = [(b.name, item) for b in bots if b.player_id for item in order if (b.player_id, item) not in server]
    unacked = [k for k in server if k not in acked]
    differ = [k for k, v in acked.items() if server.get(k) != v]
    if lost:
        fails.append(f"{len(lost)} answers missing from the engine: {lost[:5]}")
    if unacked:
        fails.append(f"{len(unacked)} recorded answers never acked to their bot: {unacked[:5]}")
    if differ:
        fails.append(f"{len(differ)} acks differ from the record: {differ[:5]}")

    expected = {b.player_id: sum(points(questions[i], c in questions[i].correct, ms) for i, (c, ms) in b.acks.items())
                for b in bots if b.player_id}
    replayed = {s.player_id: s.score for s in game.standings(questions)}
    if result["podium"] != expected:
        bad = [k for k in expected if result["podium"].get(k) != expected[k]]
        fails.append(f"podium ≠ the bots' own scoring for {len(bad)} players, e.g. {bad[:3]}")
    if replayed != expected:
        fails.append("the replay of quiz.jsonl ≠ the bots' own scoring")

    restart = result["restart"]
    if restart:
        item = restart["item"]
        before, after = restart["board_before"], restart["board_after"]
        for b in bots:
            c, ms = b.acks.get(item, (0, 0))
            gain = points(questions[item], c in questions[item].correct, ms) if item in b.acks else 0
            if after.get(b.player_id) != before.get(b.player_id, 0) + gain:
                fails.append(f"restart: {b.name} had {before.get(b.player_id)} + {gain}, now {after.get(b.player_id)}")
    fails.extend(f"unexpected: {e}" for e in stats.errors)
    return fails


def report(result: dict[str, Any], stats: Stats, fails: list[str], elapsed: float) -> None:
    bots: list[Bot] = result["bots"]
    answers = sum(len(b.acks) for b in bots)
    via_view = sum(1 for b in bots for v in b.ack_via.values() if v == "view")
    logger.info("")
    logger.info("── quiz_bots: %d bots · %d questions · %.0f s ──", len(bots), len(result["order"]), elapsed)
    logger.info("joins          %d of %d (each exactly once)", sum(1 for b in bots if b.player_id), len(bots))
    logger.info("answers acked  %d (%d by the reply, %d by the view after a reload)", answers, answers - via_view, via_view)
    logger.info("reloads        %d (transport switched %d times) · ended on ws %d, poll %d", sum(b.drops for b in bots),
                sum(b.switches for b in bots), sum(1 for b in bots if b.transport == "ws"),
                sum(1 for b in bots if b.transport == "poll"))
    for name, xs in stats.latency.items():
        label = {"join": "join", "push": "host next → question on the phone", "ack": "answer sent → acked"}[name]
        logger.info("latency %-4s   %s   (%s)", name, percentiles(xs), label)
    for key, n in sorted(stats.counts.items()):
        logger.info("count          %-40s %d", key, n)
    if result["restart"]:
        r = result["restart"]
        logger.info("restart        killed mid-question %s, back and live again after %.1f s", r["item"], r["down_s"])
    if fails:
        for f in fails:
            logger.error("❌ %s", f)
        logger.error("❌ %d check(s) failed", len(fails))
    else:
        logger.info("✅ every bot joined once; every answer recorded once, acked and scored once; "
                    "podium = replay = the bots' own scoring; 0 unexpected errors")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bots", type=int, default=60)
    p.add_argument("--questions", type=int, default=10)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--drop", type=float, default=0.15, help="chance per bot and question of a reload")
    p.add_argument("--poll-share", type=float, default=0.3, help="share of bots that start on polling")
    p.add_argument("--join-spread", type=float, default=8.0, help="bots join over this many seconds")
    p.add_argument("--restart-mid-question", action="store_true")
    p.add_argument("--restart-at", type=int, default=5, help="the question to crash on")
    p.add_argument("--reactivate-after", type=float, default=4.0, help="seconds between the restart and going live")
    p.add_argument("--port", type=int, default=0, help="the disposable app's port (default: a free one)")
    p.add_argument("--player-port", type=int, default=0, help="its player listener's port (fix it for a Funnel path)")
    p.add_argument("--player-base", default="", help="the bots' /play base URL (default: the loopback listener)")
    p.add_argument("--public-dns", action="store_true", help="resolve --player-base's host through public DNS")
    p.add_argument("--keep", action="store_true", help="keep the disposable folder")
    args = p.parse_args(argv)
    if args.port in LIVE_PORTS or args.player_port in LIVE_PORTS:
        p.error(f"ports {LIVE_PORTS} belong to the live app — the bots only play a disposable instance")
    base = urlsplit(args.player_base)
    if args.player_base and base.hostname not in ("127.0.0.1", "localhost") and base.path.rstrip("/") in ("", "/play"):
        p.error("a public --player-base must be a temporary Funnel path (e.g. …:10000/fs-bots56), "
                "never the live player at the root")
    if args.restart_mid_question and not 1 <= args.restart_at <= args.questions:
        p.error("--restart-at must be one of the questions")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx2", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = parse_args(argv)
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    if args.public_dns and args.player_base:
        pin_to_public_dns(args.player_base)
    root = Path(tempfile.mkdtemp(prefix="fs-bots-"))
    folder = root / "sessions" / "demo" / "quiz-bots"
    section = bots_quiz(args.questions, rng, args.restart_at if args.restart_mid_question else None)
    sid, _ = build_demo_session(folder, root / "sessions.local.yaml")
    add_quiz_section(folder, section)
    server = Disposable(root, args.port, args.player_port)
    logger.info("ℹ️ disposable instance under %s (seed %d)", root, seed)
    server.start()
    logger.info("ℹ️ app %s · player listener 127.0.0.1:%d", server.base, server.player_port)
    stats = Stats()
    started = time.monotonic()
    try:
        result = asyncio.run(play(args, server, sid, section, stats, rng))
    finally:
        server.stop()
    fails = check(result, folder, stats)
    report(result, stats, fails, time.monotonic() - started)
    if args.keep:
        logger.info("ℹ️ kept %s", root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

