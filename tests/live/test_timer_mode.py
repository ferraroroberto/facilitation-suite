"""The item timer's mode: one derive function, read the same way everywhere (docs/timer-state.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from src.config import load_config
from src.live.hub import LiveHub, now_ms
from src.live.timer_mode import MEANINGS, InconsistentTimer, TimerMode, TimerState, restore_timer, timer_mode
from src.sessions.store import SessionStore
from tests.fixtures.demo import build_demo_session

ROWS: list[tuple[Optional[TimerState], TimerMode]] = [
    (None, TimerMode.IDLE),
    (TimerState(total=60), TimerMode.PAUSED),
    (TimerState(total=60, elapsed=10.0), TimerMode.PAUSED),
    (TimerState(total=60, running_since=1_000), TimerMode.RUNNING),
    (TimerState(total=60, elapsed=60.0, done=True), TimerMode.DONE),
    (TimerState(total=0, done=True), TimerMode.DONE),
]


@pytest.mark.parametrize(("observation", "mode"), ROWS)
def test_every_reachable_observation_has_one_mode(observation: Optional[TimerState], mode: TimerMode) -> None:
    assert timer_mode(observation) is mode


def test_the_mode_set_is_closed_and_every_mode_says_what_it_means() -> None:
    assert {m.value for m in TimerMode} == {"idle", "running", "paused", "done"}
    assert set(MEANINGS) == set(TimerMode) and all(MEANINGS.values())


# state-machine-proof: item-timer
def test_an_impossible_timer_is_refused_not_folded_into_a_neighbour() -> None:
    with pytest.raises(InconsistentTimer):
        timer_mode(TimerState(total=60, elapsed=60.0, running_since=1_000, done=True))  # done and counting
    with pytest.raises(InconsistentTimer):
        timer_mode(TimerState(total=-5))  # a negative length


def test_the_dict_form_keeps_the_old_fields_and_adds_the_mode() -> None:
    d = TimerState(total=60, elapsed=10.0, running_since=None).as_dict()
    assert d == {"total": 60, "elapsed": 10.0, "running_since": None, "done": False, "mode": "paused"}


@pytest.fixture
def demo(isolated_env: Path) -> tuple[str, Path]:
    return build_demo_session(isolated_env / "sessions" / "demo" / "timer", isolated_env / "sessions.local.yaml")


def snap_mode(hub: LiveHub, item_id: str) -> str:
    """What a client reads for one item: the entry's ``mode``, or no entry (idle)."""
    entry = hub.snapshot()["state"]["timers"].get(item_id)
    return entry["mode"] if entry else "idle"


def test_the_snapshot_carries_the_mode_at_every_step(demo: tuple[str, Path]) -> None:
    hub = LiveHub(SessionStore(load_config()))
    hub.activate(demo[0])
    hub.goto(1)  # act-map: 120 s, started with the capture
    assert snap_mode(hub, "act-map") == "idle" and "act-map" not in hub.snapshot()["state"]["timers"]
    hub.timer_toggle()
    assert snap_mode(hub, "act-map") == "running"
    hub.timer_toggle()
    assert snap_mode(hub, "act-map") == "paused"
    hub.timer_toggle()
    assert snap_mode(hub, "act-map") == "running"
    hub.timers["act-map"].running_since = now_ms() - 121_000
    hub._timer_end("act-map")
    assert snap_mode(hub, "act-map") == "done"
    hub.timer_add_minute()  # one more minute after the end: a fresh running minute
    assert snap_mode(hub, "act-map") == "running"
    hub.timer_add_minute()  # while running it stays running
    assert snap_mode(hub, "act-map") == "running"
    hub.timer_toggle()
    hub.timer_add_minute()  # while paused it stays paused
    assert snap_mode(hub, "act-map") == "paused"
    hub.timer_reset()
    assert snap_mode(hub, "act-map") == "idle"


def test_a_pause_or_start_from_another_service_follows_the_mode(demo: tuple[str, Path]) -> None:
    hub = LiveHub(SessionStore(load_config()))
    hub.activate(demo[0])
    hub.pause_timer_for("act-map")  # nothing running: no entry appears
    assert snap_mode(hub, "act-map") == "idle"
    hub.start_timer_for("act-map")
    assert snap_mode(hub, "act-map") == "running"
    hub.start_timer_for("act-map")  # already running: untouched
    assert snap_mode(hub, "act-map") == "running"
    hub.pause_timer_for("act-map")
    assert snap_mode(hub, "act-map") == "paused"


def edit_state(folder: Path, change: Any) -> None:
    path = folder / "live" / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_restart_restores_each_mode_and_normalises_the_impossible_ones(
        demo: tuple[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    sid, folder = demo
    first = LiveHub(SessionStore(load_config()))
    first.activate(sid)
    first.goto(1)
    first.timer_toggle()  # writes live/state.json

    def timers(d: dict[str, Any]) -> None:  # the file as an older build wrote it: no `mode` key
        old = {"total": 60, "elapsed": 0, "running_since": None, "done": False}
        d["timers"] = {
            "act-map": {**old, "running_since": 1_000},
            "slide-105": {**old, "elapsed": 10},
            "brk-coffee": {**old, "elapsed": 60, "done": True},
            "slide-101": {**old, "elapsed": 20, "running_since": 1_000, "done": True},  # done and counting
            "slide-102": {**old, "total": -5},  # negative length
        }

    edit_state(folder, timers)
    caplog.set_level("INFO", logger="src.live.timer_mode")
    fresh = LiveHub(SessionStore(load_config()))
    fresh.activate(sid)
    assert {k: timer_mode(fresh.timers.get(k)).value for k in ("act-map", "slide-105", "brk-coffee", "slide-101", "slide-102")} == {
        "act-map": "running", "slide-105": "paused", "brk-coffee": "done", "slide-101": "done", "slide-102": "idle"}
    assert fresh.timers["slide-101"].running_since is None and fresh.timers["slide-101"].elapsed == 60  # the end won
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "slide-101" in logged and "slide-102" in logged  # both normalisations leave a breadcrumb
    assert snap_mode(fresh, "slide-101") == "done"


def test_restore_timer_reads_a_file_without_a_mode_key() -> None:
    t = restore_timer("x", {"total": 90, "elapsed": 5, "running_since": None, "done": False})
    assert t is not None and timer_mode(t) is TimerMode.PAUSED
