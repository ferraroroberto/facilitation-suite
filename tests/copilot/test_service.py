"""The copilot end to end inside the live hub: two synthetic WAV channels → chunks → the (fake) hub →
transcript.jsonl with speaker labels → hints; stale hints dropped; failures told apart."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import CopilotConfig, load_config
from src.copilot.audio import read_pcm, write_wav
from src.copilot.service import CopilotError, CopilotService
from src.jsonl import read_jsonl
from src.live.actions import ACTIONS, Action, run_action
from src.live.hub import LiveHub
from src.sessions.store import SessionStore
from tests.copilot.synth import speech
from tests.fixtures.demo import build_demo_session
from tests.fixtures.fake_hub import FakeHub

# Short chunks so a test runs in seconds: 1 s minimum, a 200 ms pause cuts.
FAST = {"min_chunk_s": 1, "max_chunk_s": 3, "silence_ms": 200, "hint_interval_s": 0}


def wait_for(cond: Callable[[], object], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


@pytest.fixture
def hub_fake() -> Iterator[FakeHub]:
    fake = FakeHub()
    yield fake
    fake.close()


@pytest.fixture
def live(isolated_env: Path) -> LiveHub:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "live", isolated_env / "sessions.local.yaml")
    hub = LiveHub(SessionStore(load_config()))
    hub.activate(sid)
    return hub


def make(live: LiveHub, **cfg: object) -> tuple[CopilotService, list[CopilotConfig]]:
    box = [replace(CopilotConfig(), **cfg)]
    return CopilotService(live, lambda: box[0]), box


def test_two_channels_become_a_labelled_transcript_and_hints(live: LiveHub, hub_fake: FakeHub, tmp_path: Path) -> None:
    write_wav(tmp_path / "me.wav", speech([("voice", 1.2), ("pause", 0.4)]))
    write_wav(tmp_path / "them.wav", speech([("pause", 0.2), ("voice", 1.2), ("pause", 0.4)]))
    hub_fake.transcripts += ["So which idea first?", "The cheapest one, I think."]
    copilot, _ = make(live, hub_url=hub_fake.url, capture="files", feed_dir=str(tmp_path), **FAST)
    copilot.start()
    folder = live.folder / "live" / "copilot"
    wait_for(lambda: len(read_jsonl(folder / "transcript.jsonl")) == 2)
    lines = read_jsonl(folder / "transcript.jsonl")
    assert sorted(line["speaker"] for line in lines) == ["me", "them"]
    assert all(line["t1"] > line["t0"] >= 0 and line["lag_ms"] >= 0 and line["text"] for line in lines)
    calls = hub_fake.calls_to("/v1/audio/transcriptions")
    assert len(calls) == 2 and {c["language"] for c in calls} == {"en"} and {c["model"] for c in calls} == {"whisper"}
    # the "them" line asked for hints with the plan's prefix and the line itself
    wait_for(lambda: copilot.snapshot()["hints"])
    snap = copilot.snapshot()
    assert snap["hints"][0]["type"] == "next_q" and snap["hints_model"] == "claude_haiku"
    prompt = hub_fake.calls_to("/v1/chat/completions")[0]["messages"][1]["content"]
    assert "On stage now:" in prompt and "them: " in prompt
    record = read_jsonl(folder / "hints.jsonl")[0]
    assert record["model"] == "claude_haiku" and record["lag_ms"] >= 0 and record["reason"] == "them"
    copilot.stop()
    assert not copilot.snapshot()["on"]
    # both channels were kept on disk as they arrived (the last part with the stop)
    wait_for(lambda: len(list(folder.glob("*.wav"))) == 2 and all(len(read_pcm(p)) > 40000 for p in folder.glob("*.wav")))


def test_an_item_change_clears_hints_and_drops_an_answer_for_the_old_item(live: LiveHub, hub_fake: FakeHub) -> None:
    copilot, _ = make(live, hub_url=hub_fake.url, capture="off", **FAST)
    copilot.start()
    copilot.inject([("them", "We are running out of time")])
    wait_for(lambda: copilot.snapshot()["hints"])
    hub_fake.delay_s = 0.6
    copilot.inject([("them", "One more thing")])  # its answer comes back after the item changed
    live.next()
    assert copilot.snapshot()["hints"] == []  # cleared at once: they were for the item that left
    wait_for(lambda: len(read_jsonl(live.folder / "live" / "copilot" / "hints.jsonl")) >= 3)
    records = read_jsonl(live.folder / "live" / "copilot" / "hints.jsonl")
    assert any(r.get("dropped") == "stale" for r in records)
    assert {r["reason"] for r in records} >= {"inject", "item"}
    wait_for(lambda: copilot.snapshot()["hints_item_id"] == live.current()["id"])  # the new item's own hints


def test_local_only_asks_the_local_model_alone(live: LiveHub, hub_fake: FakeHub) -> None:
    copilot, _ = make(live, hub_url=hub_fake.url, capture="off", **FAST)
    copilot.set_local_only(True)
    assert live.store.load(live.session_id).copilot_local_only is True  # saved in session.yaml
    copilot.start()
    copilot.inject([("them", "This stays on this machine")])
    wait_for(lambda: copilot.snapshot()["hints"])
    assert [c["model"] for c in hub_fake.calls_to("/v1/chat/completions")] == ["agentic_light"]


def test_failures_are_kept_apart_and_clear(live: LiveHub, hub_fake: FakeHub) -> None:
    copilot, box = make(live, hub_url="http://127.0.0.1:9", capture="off", **FAST)
    copilot.start()
    copilot.inject([("them", "Hello")])
    wait_for(lambda: copilot.snapshot()["problems"])
    assert [p["code"] for p in copilot.snapshot()["problems"]] == ["hub_down"]
    box[0] = replace(box[0], hub_url=hub_fake.url, hint_timeout_s=1)
    hub_fake.delay_s = 1.5
    copilot.inject([("them", "Hello again")])
    wait_for(lambda: [p["code"] for p in copilot.snapshot()["problems"]] == ["hint_timeout"])
    hub_fake.delay_s = 0
    hub_fake.failing |= {"claude_haiku", "gemini_flash_lite", "agentic_light"}
    copilot.inject([("them", "And again")])
    wait_for(lambda: [p["code"] for p in copilot.snapshot()["problems"]] == ["hint_error"])
    hub_fake.failing.clear()
    copilot.inject([("them", "Fine now")])
    wait_for(lambda: copilot.snapshot()["problems"] == [] and copilot.snapshot()["hints"])


def test_a_channel_that_cannot_open_is_shown_and_the_rest_carries_on(live: LiveHub, hub_fake: FakeHub, tmp_path: Path) -> None:
    write_wav(tmp_path / "them.wav", speech([("voice", 1.2), ("pause", 0.4)]))  # no me.wav
    copilot, _ = make(live, hub_url=hub_fake.url, capture="files", feed_dir=str(tmp_path), **FAST)
    copilot.start()
    wait_for(lambda: {c["speaker"]: c["state"] for c in copilot.snapshot()["channels"]} == {"me": "error", "them": "recording"})
    assert "not available" in copilot.snapshot()["channels"][0]["detail"]
    wait_for(lambda: copilot.snapshot()["stats"]["lines"] == 1)
    copilot.stop()


def test_toggle_is_a_live_action_and_closing_the_session_stops_it(live: LiveHub, monkeypatch: pytest.MonkeyPatch) -> None:
    copilot, _ = make(live, capture="off", hub_url="")
    monkeypatch.setitem(ACTIONS, "copilot_toggle", Action("copilot_toggle", "Copilot on/off", lambda hub, arg: copilot.toggle()))
    run_action(live, "copilot_toggle")
    assert copilot.on and copilot.snapshot()["problems"][0]["code"] == "no_hub"
    with pytest.raises(CopilotError):
        CopilotService(LiveHub(live.store), lambda: CopilotConfig(capture="off", hub_url="")).start()  # nothing live
    folder = live.folder
    live.deactivate()
    assert not copilot.on
    assert {"copilot_on", "copilot_off"} <= {e["event"] for e in read_jsonl(folder / "live" / "events.jsonl")}


def test_a_plan_saved_from_an_older_copy_keeps_the_presenters_local_only(live: LiveHub) -> None:
    copilot, _ = make(live, capture="off", hub_url="")
    older = live.store.load(live.session_id)  # the Plan tab loaded it before the switch was flipped
    copilot.set_local_only(True)
    live.store.save_plan(live.session_id, older)
    assert live.store.load(live.session_id).copilot_local_only is True
