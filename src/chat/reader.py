"""The Zoom chat reader — its own process, so a crash never takes the server down.

    python -m src.chat.reader --server http://127.0.0.1:8449 [--parent-pid N]
    python -m src.chat.reader --server … --simulate burst:50
    python -m src.chat.reader --server … --simulate script.yaml

Every ``poll_ms`` (500 ms): find the popped-out chat window (class + title
from the config) → MSAA walk → the message rows of the "Chat messages
history" list → diff against the previous snapshot by order → POST the new
rows to ``/api/chat/messages``. A heartbeat with the state goes to
``/api/chat/heartbeat`` every second. States, each with its own message:

- ``reading``          — the window was read
- ``window_not_found`` — Zoom closed, or the chat is not popped out
- ``error``            — the walk raised (the detail says what)
- ``simulating``       — replaying a script instead of reading Zoom

(``stale`` — no heartbeat for more than 3 s — is decided by the server.)

The first successful read after start is sent as a ``baseline``: the server
lines it up with what it already stored, so a restarted reader never
duplicates history. Batches that fail to post are kept and retried in order;
each batch carries an id so a retry of a batch that did arrive is ignored.

With ``--parent-pid`` the reader exits when that process (the server) is
gone, so a restarted server never ends up with two readers. The server stops
it by writing ``--stop-file`` (a clean exit restores the screen-reader flag,
which a hard kill could not).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional

import yaml

from src.chat.parse import Row, new_rows, rows_from_names
from src.config import data_dir, load_config
from src.logger import configure_logging

logger = logging.getLogger("chat_reader")

HISTORY_LIST_PREFIX = "Chat messages history"
ROLE_LIST, ROLE_LISTITEM = 33, 34


def message_names(nodes: list[Any]) -> tuple[list[str], bool]:
    """Names of the list items inside the chat-history list (and whether that
    list was found; if not, every list item in the window is used)."""
    out: list[str] = []
    inside: Optional[int] = None
    found = False
    for n in nodes:
        if inside is not None and n.depth <= inside:
            inside = None
        if n.role == ROLE_LIST and (n.name or "").startswith(HISTORY_LIST_PREFIX):
            inside, found = n.depth, True
            continue
        if inside is not None and n.role == ROLE_LISTITEM:
            out.append(n.name or "")
    if not found:
        out = [n.name or "" for n in nodes if n.role == ROLE_LISTITEM]
    return out, found


def _parent_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


class Poster:
    """POSTs to the server; batches that fail wait in order and are retried."""

    def __init__(self, server: str) -> None:
        self.server = server.rstrip("/")
        self.pending: deque[dict[str, Any]] = deque()
        self.run_id = uuid.uuid4().hex[:8]
        self.counter = 0
        self._ctx = ssl._create_unverified_context()  # noqa: S323 — loopback to our own server only
        self.down_since: Optional[float] = None

    def _other_scheme(self) -> None:
        """The server may run HTTPS (tailnet cert) or plain HTTP: try the other."""
        a, b = ("https://", "http://") if self.server.startswith("https://") else ("http://", "https://")
        self.server = b + self.server[len(a):]

    def _post(self, path: str, body: dict[str, Any], timeout: float = 3.0) -> bool:
        req = urllib.request.Request(self.server + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as r:  # noqa: S310 — our own server
                ok = 200 <= r.status < 300
        except urllib.error.HTTPError as exc:
            logger.error("❌ server refused %s (%s) — dropping that batch", path, exc.code)
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._other_scheme()
            if self.down_since is None:
                self.down_since = time.monotonic()
                logger.warning("⚠️ server unreachable (%s) — keeping messages until it is back", exc)
            return False
        if self.down_since is not None:
            logger.info("✅ server reachable again after %.1f s", time.monotonic() - self.down_since)
            self.down_since = None
        return ok

    def messages(self, rows: list[Row], *, baseline: bool, source: str) -> None:
        if not rows and not baseline:
            return
        self.counter += 1
        now = int(time.time() * 1000)
        self.pending.append({
            "batch": f"{self.run_id}-{self.counter}", "baseline": baseline, "source": source,
            "messages": [{"sender": r.sender, "text": r.text, "time": r.time, "received_at": now} for r in rows],
        })
        self.flush()

    def flush(self) -> None:
        while self.pending:
            if not self._post("/api/chat/messages", self.pending[0]):
                return
            self.pending.popleft()

    def heartbeat(self, state: str, detail: str, **extra: Any) -> None:
        self._post("/api/chat/heartbeat", {"state": state, "detail": detail, "pid": os.getpid(),
                                           "pending": len(self.pending), **extra}, timeout=2.0)


class Reader:
    def __init__(self, poster: Poster, window_class: str, window_title: str, poll_ms: int, parent_pid: Optional[int],
                 stop_file: Optional[Path] = None) -> None:
        self.poster = poster
        self.stop_file = stop_file
        self.window_class, self.window_title = window_class, window_title
        self.poll = max(0.1, poll_ms / 1000)
        self.parent_pid = parent_pid
        self.prev: Optional[list[Row]] = None
        self.state, self.detail = "starting", ""
        self.rows = 0
        self.nodes = 0

    def _set(self, state: str, detail: str) -> None:
        if state != self.state:
            logger.info("ℹ️ reader: %s → %s (%s)", self.state, state, detail)
        self.state, self.detail = state, detail

    def read_once(self) -> None:
        from src.chat import msaa

        hwnd = msaa.find_window(self.window_class, self.window_title)
        if not hwnd:
            self._set("window_not_found", "Pop out the Zoom meeting chat (… → Pop out)")
            return
        try:
            nodes = msaa.walk(msaa.accessible_from_window(hwnd))
        except Exception as exc:  # noqa: BLE001 — COM errors vary; the detail tells them apart
            self._set("error", f"Reading the chat failed: {exc}")
            return
        names, found = message_names(nodes)
        rows = rows_from_names(names)
        self.nodes, self.rows = len(nodes), len(rows)
        if self.prev is None:
            self.poster.messages(rows, baseline=True, source="zoom")
            logger.info("ℹ️ reader: baseline of %d rows (%d nodes, history list %s)", len(rows), len(nodes),
                        "found" if found else "NOT found — using every list item")
        else:
            fresh, how = new_rows(self.prev, rows)
            if how == "realign":
                logger.warning("⚠️ reader: snapshot realigned (a message may have been deleted); %d new", len(fresh))
            self.poster.messages(fresh, baseline=False, source="zoom")
        self.prev = rows
        self._set("reading", f"{len(rows)} messages in the window")

    def run(self) -> int:
        from src.chat import msaa

        was_on = msaa.screen_reader_flag()
        if not was_on:
            msaa.set_screen_reader_flag(True)
            logger.info("ℹ️ screen-reader flag set on (was off); restored on exit")
        last_beat = 0.0
        try:
            while True:
                if self.parent_pid and not _parent_alive(self.parent_pid):
                    logger.info("ℹ️ server process %s is gone — reader exiting", self.parent_pid)
                    return 0
                if self.stop_file and self.stop_file.exists():
                    logger.info("ℹ️ stop requested by the server — reader exiting")
                    self.poster.heartbeat("stopped", "The chat reader was stopped", source="zoom")
                    return 0
                started = time.monotonic()
                self.read_once()
                self.poster.flush()
                if started - last_beat >= 1.0:
                    last_beat = started
                    self.poster.heartbeat(self.state, self.detail, rows=self.rows, nodes=self.nodes, source="zoom")
                time.sleep(max(0.0, self.poll - (time.monotonic() - started)))
        finally:
            if not was_on:
                msaa.set_screen_reader_flag(False)
                logger.info("ℹ️ screen-reader flag restored to off")


# ------------------------------------------------------------------ simulator

SIM_PEOPLE = ["Alex Rivera", "Sam Taylor", "Jordan Lee", "Casey Morgan", "Robin Diaz", "Jamie Chen",
              "Taylor Brooks", "Morgan Silva", "Avery Kim", "Riley Novak", "Quinn Park", "Drew Santos"]
SIM_ANSWERS = ["meetings", "perfectionism", "procrastination", "interruptions", "notifications", "multitasking",
               "tiredness", "no agenda", "saying yes to everything", "emails", "rushing", "comparison"]


def load_script(spec: str, answers: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """``burst:N`` (N numbered messages at once), ``random:N[:every_ms]``,
    ``list:every_ms`` (each answer once, verbatim, in order) or a YAML file
    ``{messages: [{after: seconds, sender, text}]}``."""
    pool = answers or SIM_ANSWERS
    if spec.startswith("list:"):
        every = int(spec.split(":")[1]) / 1000
        return [{"after": every if i else 0, "sender": SIM_PEOPLE[i % len(SIM_PEOPLE)], "text": t} for i, t in enumerate(pool)]
    if spec.startswith("burst:"):
        n = int(spec.split(":")[1])
        return [{"after": 0, "sender": SIM_PEOPLE[i % len(SIM_PEOPLE)], "text": f"{pool[i % len(pool)]} {i + 1}"} for i in range(n)]
    if spec.startswith("random:"):
        bits = spec.split(":")
        n, every = int(bits[1]), (int(bits[2]) if len(bits) > 2 else 700) / 1000
        rnd = random.Random(7)
        return [{"after": every * rnd.uniform(0.3, 1.7), "sender": rnd.choice(SIM_PEOPLE), "text": rnd.choice(pool)} for _ in range(n)]
    data = yaml.safe_load(Path(spec).read_text(encoding="utf-8")) or {}
    return list(data.get("messages") or [])


def simulate(poster: Poster, script: list[dict[str, Any]], parent_pid: Optional[int]) -> int:
    logger.info("ℹ️ simulator: %d messages", len(script))
    poster.heartbeat("simulating", f"Replaying {len(script)} simulated messages", source="simulator")
    batch: list[Row] = []
    last_beat = time.monotonic()
    for i, m in enumerate(script):
        wait = float(m.get("after", 0) or 0)
        if wait > 0 and batch:
            poster.messages(batch, baseline=False, source="simulator")
            batch = []
        if wait > 0:
            time.sleep(wait)
        if parent_pid and not _parent_alive(parent_pid):
            return 0
        batch.append(Row(sender=str(m.get("sender") or "Guest"), text=str(m.get("text") or ""), time=time.strftime("%H:%M")))
        if time.monotonic() - last_beat >= 1.0:
            last_beat = time.monotonic()
            poster.heartbeat("simulating", f"Simulated {i + 1} of {len(script)}", source="simulator")
    poster.messages(batch, baseline=False, source="simulator")
    for _ in range(20):
        if not poster.pending:
            break
        time.sleep(0.5)
        poster.flush()
    poster.heartbeat("stopped", "Simulation finished", source="simulator")
    logger.info("✅ simulator: done (%d unsent)", len(poster.pending))
    return 0 if not poster.pending else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read the popped-out Zoom chat (or simulate it) and post to the server.")
    ap.add_argument("--server", required=True)
    ap.add_argument("--parent-pid", type=int)
    ap.add_argument("--simulate", help="burst:N | random:N[:every_ms] | script.yaml")
    ap.add_argument("--answers", help="comma-separated answers for burst/random simulations")
    ap.add_argument("--stop-file", type=Path, help="exit cleanly when this file appears")
    args = ap.parse_args(argv)
    configure_logging(log_file=data_dir() / "logs" / "chat-reader.log")
    poster = Poster(args.server)
    if args.simulate:
        answers = [a.strip() for a in args.answers.split(",") if a.strip()] if args.answers else None
        return simulate(poster, load_script(args.simulate, answers), args.parent_pid)
    if sys.platform != "win32":
        logger.error("❌ the chat reader needs Windows (MSAA)")
        return 2
    cfg = load_config().reader
    logger.info("✅ reader up — window %r / %r every %d ms, posting to %s", cfg.window_class, cfg.window_title, cfg.poll_ms, args.server)
    return Reader(poster, cfg.window_class, cfg.window_title, cfg.poll_ms, args.parent_pid, args.stop_file).run()


if __name__ == "__main__":
    raise SystemExit(main())
