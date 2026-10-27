"""The chat reader (Zoom, or Teams with ``reader.source = "teams"``, #237;
the server passes the presenter's choice as ``--source``, #239) — its own
process, so a crash never takes the server down.

    python -m src.chat.reader --server http://127.0.0.1:8449 [--parent-pid N] [--source zoom|teams]
    python -m src.chat.reader --server … --simulate burst:50
    python -m src.chat.reader --server … --simulate script.yaml

Every ``poll_ms`` (500 ms): find the popped-out chat window (class + title
from the config) → MSAA walk → the message rows of the "Chat messages
history" list → diff against the previous snapshot by order → POST the new
rows to ``/api/chat/messages``. With ``reader.source = "teams"`` the window is
the one titled ``… Microsoft Teams`` showing a chat, read through UI Automation
(``src/chat/uia.py`` → ``src/chat/teams.py``); everything after the snapshot —
diff, baseline, retry, heartbeat, stop file — is shared. A heartbeat with the
state and the last scan's duration (``scan_ms``) goes to ``/api/chat/heartbeat``
every second from a thread of its own, so a scan that takes seconds never reads
as a silent reader; a scan slower than ``poll_ms`` is logged with its node and
row counts. In Zoom, once a full walk has found the history list and reading
its last children is shown to give the same rows, later scans read only the
children added since the last one (plus a few already seen to line them up);
anything that does not line up falls back to walking the whole window. States,
each with its own message:

- ``reading``          — the window was read
- ``window_not_found`` — Zoom closed, or the chat is not popped out (Teams:
  no window shows a chat)
- ``error``            — the walk raised (the detail says what)
- ``simulating``       — replaying a script instead of reading Zoom

(``stale`` — no heartbeat for more than 3 s — is decided by the server.)

The first successful read after start is sent as a ``baseline``: the server
lines it up with what it already stored, so a restarted reader never
duplicates history. The same goes for a window that comes back (Zoom hides
the popped-out chat around breakout rooms): after ``window_not_found``, an
error, or a list that went empty, the next non-empty read is a baseline too,
and so is a snapshot with nothing in common with the previous one. Batches that fail to post are kept and retried in order;
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
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import yaml

from src.chat.parse import Row, new_rows, rows_from_names
from src.config import load_config
from src.logger import configure_logging, log_path

logger = logging.getLogger("chat_reader")

SOURCES = ("zoom", "teams")
HISTORY_LIST_PREFIX = "Chat messages history"
ROLE_LIST, ROLE_LISTITEM = 33, 34
# Tail reads: the already-seen children a scan re-reads to line the new ones up, the most it reads
# before the whole window is walked instead, and how many the check of a newly found list reads.
TAIL_OVERLAP, TAIL_MAX, TAIL_PROBE = 4, 60, 10
BEAT_EVERY_S = 1.0  # the server calls the reader stale after 3 s without a heartbeat


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

    def _other_scheme(self, failed: str) -> None:
        """The server may run HTTPS (tailnet cert) or plain HTTP: try the other. Two threads
        post (the scan loop and the heartbeat), so only the one that used the current address flips it."""
        if failed != self.server:
            return
        a, b = ("https://", "http://") if self.server.startswith("https://") else ("http://", "https://")
        self.server = b + self.server[len(a):]

    def _post(self, path: str, body: dict[str, Any], timeout: float = 3.0) -> bool:
        server = self.server
        req = urllib.request.Request(server + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as r:  # noqa: S310 — our own server
                ok = 200 <= r.status < 300
        except urllib.error.HTTPError as exc:
            logger.error("❌ server refused %s (%s) — dropping that batch", path, exc.code)
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._other_scheme(server)
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
                 stop_file: Optional[Path] = None, source: str = "zoom", teams_title: str = "Microsoft Teams") -> None:
        self.poster = poster
        self.stop_file = stop_file
        self.window_class, self.window_title = window_class, window_title
        self.source, self.teams_title = source, teams_title
        self.poll = max(0.1, poll_ms / 1000)
        self.parent_pid = parent_pid
        self.prev: Optional[list[Row]] = None
        self.rebaseline = False  # the window was lost: its next non-empty read is history, not new rows
        self.prev_ids: set[str] = set()
        self.hwnd: Optional[int] = None
        self.state, self.detail = "starting", ""
        self.rows = 0
        self.nodes = 0
        self.scan_ms = 0
        self.clock: Callable[[], float] = time.monotonic
        self.beat_every = BEAT_EVERY_S
        self.list_acc: Optional[Any] = None  # the history list, once reading only its tail is proven safe
        self.list_count = 0
        self.list_hwnd: Optional[int] = None

    def _set(self, state: str, detail: str) -> None:
        if state != self.state:
            logger.info("ℹ️ reader: %s → %s (%s)", self.state, state, detail)
        self.state, self.detail = state, detail

    def _lost(self, state: str, detail: str) -> None:
        """No clean read: diffing the window's return against the last snapshot
        would post its whole history as new, so the next read starts over."""
        self.list_acc = None
        if self.prev is not None:
            logger.info("ℹ️ reader: lost the chat (%s) with %d rows read — the next read re-baselines", state, len(self.prev))
            self.prev, self.rebaseline = None, True
        self._set(state, detail)

    def read_once(self) -> None:
        """One scan of the chat, timed: a scan slower than the poll interval leaves a log line."""
        started = self.clock()
        try:
            if self.source == "teams":
                self._read_teams()
            else:
                self._read_zoom()
        finally:
            self.scan_ms = round((self.clock() - started) * 1000)
            if self.scan_ms > self.poll * 1000:
                logger.info("ℹ️ reader: slow scan — %d ms for %d nodes, %d rows (poll every %d ms)",
                            self.scan_ms, self.nodes, self.rows, round(self.poll * 1000))

    def _read_zoom(self) -> None:
        from src.chat import msaa

        hwnd = msaa.find_window(self.window_class, self.window_title)
        if not hwnd:
            self._lost("window_not_found", "Pop out the Zoom meeting chat (… → Pop out)")
            return
        if hwnd != self.list_hwnd:
            self.list_acc = None
        try:
            if self._read_tail(msaa):
                return
            root = msaa.accessible_from_window(hwnd)
            nodes = msaa.walk(root)
        except Exception as exc:  # noqa: BLE001 — COM errors vary; the detail tells them apart
            self._lost("error", f"Reading the chat failed: {exc}")
            return
        names, found = message_names(nodes)
        rows = rows_from_names(names)
        self._cache_list(msaa, root, hwnd, rows if found else [])
        self._publish(rows, len(nodes), f"history list {'found' if found else 'NOT found — using every list item'}")

    def _cache_list(self, msaa: Any, root: Any, hwnd: int, rows: list[Row]) -> None:
        """Keep the history list so the next scans read only its tail, once reading
        the tail is shown to give the same rows the full walk just did."""
        self.list_acc = None
        if not rows:
            return
        try:
            lst = msaa.find_list(root, HISTORY_LIST_PREFIX)
            if lst is None:
                return
            count = msaa.list_count(lst)
            probe = rows_from_names(msaa.tail_names(lst, count, TAIL_PROBE))
            if not probe or probe != rows[-len(probe):]:
                logger.info("ℹ️ reader: the list's last children do not match the full walk — reading the whole window")
                return
        except Exception as exc:  # noqa: BLE001 — a probe that fails leaves the full walk in charge
            logger.info("ℹ️ reader: could not set up tail reads (%s) — reading the whole window", exc)
            return
        self.list_acc, self.list_count, self.list_hwnd = lst, count, hwnd
        logger.info("ℹ️ reader: reading only the tail of the history list from now on (%d children)", count)

    def _read_tail(self, msaa: Any) -> bool:
        """Read just the children added since the last scan (plus a few already seen to line
        them up). False when that cannot be trusted — the caller walks the whole window."""
        if self.list_acc is None or not self.prev:
            return False
        try:
            count = msaa.list_count(self.list_acc)
            n = max(0, count - self.list_count) + TAIL_OVERLAP
            if count < self.list_count or n > TAIL_MAX:
                raise ValueError(f"list went from {self.list_count} to {count} children")
            tail = rows_from_names(msaa.tail_names(self.list_acc, count, n))
            fresh, how = new_rows(self.prev, tail)
            if not tail or how != "append" or len(fresh) >= len(tail) or len(fresh) > count - self.list_count:
                raise ValueError("the tail does not line up with the last snapshot")
        except Exception as exc:  # noqa: BLE001 — the list may be gone (COM error) or the tail unusable
            logger.info("ℹ️ reader: tail read dropped (%s) — walking the whole window", exc)
            self.list_acc = None
            return False
        self.list_count = count
        self._publish(self.prev + fresh, n, "tail of the history list")
        return True

    def _publish(self, rows: list[Row], nodes: int, note: str) -> None:
        """The shared tail of a read: the first snapshot is the baseline, later
        ones post only what follows the previous snapshot."""
        self.nodes, self.rows = nodes, len(rows)
        if not rows and (self.prev or self.rebaseline):
            # The list empties as the window goes and as it comes back; the chat
            # itself never does, so diffing the next read against [] would post it all.
            if self.prev:
                logger.info("ℹ️ reader: the chat list went empty after %d rows — the next read re-baselines", len(self.prev))
            self.prev, self.rebaseline = None, True
            self._set("reading", "0 messages in the window")
            return
        if self.prev is None:
            self.poster.messages(rows, baseline=True, source=self.source)
            if self.rebaseline:
                logger.info("ℹ️ reader: the chat is back — re-baselined on its %d rows (%d nodes, %s)", len(rows), nodes, note)
            else:
                logger.info("ℹ️ reader: baseline of %d rows (%d nodes, %s)", len(rows), nodes, note)
            self.rebaseline = False
        else:
            fresh, how = new_rows(self.prev, rows)
            if how == "unrelated":
                # The server lines it up with what it stored: the main chat back after
                # another one posts only what is new, a different chat posts nothing.
                self.poster.messages(rows, baseline=True, source=self.source)
                logger.info("ℹ️ reader: snapshot shares nothing with the last one — re-baselined on its %d rows", len(rows))
            else:
                if how == "realign":
                    logger.warning("⚠️ reader: snapshot realigned (a message may have been deleted); %d new", len(fresh))
                self.poster.messages(fresh, baseline=False, source=self.source)
        self.prev = rows
        self._set("reading", f"{len(rows)} messages in the window")

    def _read_teams(self) -> None:
        from src.chat import teams, uia

        try:
            hwnds = teams.candidates(uia.visible_titles(), self.teams_title)
            if self.hwnd in hwnds:  # stay with the window that worked last time
                hwnds.remove(self.hwnd)
                hwnds.insert(0, self.hwnd)
            nodes = None
            for hwnd in hwnds:
                nodes = uia.chat_list(hwnd)
                if nodes is not None:
                    self.hwnd = hwnd
                    break
        except Exception as exc:  # noqa: BLE001 — COM errors vary; the detail tells them apart
            self._lost("error", f"Reading the Teams chat failed: {exc}")
            return
        if not hwnds:
            self._lost("window_not_found", "Open the Teams meeting chat (in a browser, as the active tab of its window)")
            return
        if nodes is None:
            self._lost("window_not_found", "Teams is open but shows no chat: open the meeting chat")
            return
        pairs, _ = teams.rows_from_tree(nodes)
        ids = {mid for mid, _ in pairs}
        rows = [r for _, r in pairs]
        if self.prev is not None and self.prev_ids and ids and not ids & self.prev_ids:
            # Not one message in common with the last read: the facilitator opened
            # another chat. Its history is not new — start over from it.
            logger.warning("⚠️ reader: the Teams window now shows another chat; its %d messages are history", len(rows))
            self.prev = rows
        self.prev_ids = ids
        self._publish(rows, len(nodes), "Teams chat list found")

    def _beat_loop(self, stop: threading.Event) -> None:
        """The "alive" signal, on its own clock: a slow scan shows as a large ``scan_ms``,
        never as a reader the server stopped hearing from."""
        while True:
            try:
                self.poster.heartbeat(self.state, self.detail, rows=self.rows, nodes=self.nodes,
                                      scan_ms=self.scan_ms, source=self.source)
            except Exception:  # noqa: BLE001 — a failed beat must not end the thread; the next one retries
                logger.exception("❌ reader heartbeat failed")
            if stop.wait(self.beat_every):
                return

    def run(self) -> int:
        from src.chat import msaa

        was_on = msaa.screen_reader_flag()
        if not was_on:
            msaa.set_screen_reader_flag(True)
            logger.info("ℹ️ screen-reader flag set on (was off); restored on exit")
        stop_beat = threading.Event()
        beat = threading.Thread(target=self._beat_loop, args=(stop_beat,), name="chat-reader-heartbeat", daemon=True)
        beat.start()
        try:
            while True:
                if self.parent_pid and not _parent_alive(self.parent_pid):
                    logger.info("ℹ️ server process %s is gone — reader exiting", self.parent_pid)
                    return 0
                if self.stop_file and self.stop_file.exists():
                    logger.info("ℹ️ stop requested by the server — reader exiting")
                    stop_beat.set()
                    beat.join(self.beat_every + 5)  # a late "reading" beat must not land after "stopped"
                    self.poster.heartbeat("stopped", "The chat reader was stopped", source=self.source)
                    return 0
                started = time.monotonic()
                self.read_once()
                self.poster.flush()
                time.sleep(max(0.0, self.poll - (time.monotonic() - started)))
        finally:
            stop_beat.set()
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
    ap = argparse.ArgumentParser(description="Read the Zoom or Teams meeting chat (or simulate it) and post to the server.")
    ap.add_argument("--server", required=True)
    ap.add_argument("--parent-pid", type=int)
    ap.add_argument("--simulate", help="burst:N | random:N[:every_ms] | script.yaml")
    ap.add_argument("--answers", help="comma-separated answers for burst/random simulations")
    ap.add_argument("--stop-file", type=Path, help="exit cleanly when this file appears")
    ap.add_argument("--source", choices=SOURCES, help="the chat to read (default: the config's reader.source)")
    args = ap.parse_args(argv)
    configure_logging(log_file=log_path("chat-reader"))
    poster = Poster(args.server)
    if args.simulate:
        answers = [a.strip() for a in args.answers.split(",") if a.strip()] if args.answers else None
        return simulate(poster, load_script(args.simulate, answers), args.parent_pid)
    if sys.platform != "win32":
        logger.error("❌ the chat reader needs Windows (MSAA)")
        return 2
    cfg = load_config().reader
    source = args.source or (cfg.source if cfg.source in SOURCES else "zoom")
    if not args.source and source != cfg.source:
        logger.warning("⚠️ config reader.source %r is not one of %s — reading Zoom", cfg.source, ", ".join(SOURCES))
    if source == "teams":
        logger.info("✅ reader up — Teams, a window titled …%r… every %d ms, posting to %s", cfg.teams_title, cfg.poll_ms, args.server)
    else:
        logger.info("✅ reader up — window %r / %r every %d ms, posting to %s", cfg.window_class, cfg.window_title, cfg.poll_ms, args.server)
    return Reader(poster, cfg.window_class, cfg.window_title, cfg.poll_ms, args.parent_pid, args.stop_file,
                  source=source, teams_title=cfg.teams_title).run()


if __name__ == "__main__":
    raise SystemExit(main())
