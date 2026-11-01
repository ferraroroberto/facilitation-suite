"""Hints: the strict JSON, the clock sentence, the model chain; and the hub client's three failures."""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from src.copilot.hints import (
    HintFormatError,
    Line,
    ask_chain,
    clock_state,
    parse_hints,
    system_prompt,
    user_prompt,
)
from src.copilot.hub_client import HubDown, HubError, HubTimeout, complete, transcribe
from tests.copilot.synth import tone
from tests.fixtures.fake_hub import FakeHub


@pytest.fixture
def hub() -> Iterator[FakeHub]:
    fake = FakeHub()
    yield fake
    fake.close()


def test_parse_takes_strict_json_and_trims_it() -> None:
    text = '```json\n{"hints": [{"type": "next_q", "text": "one two three four five six seven eight nine ten eleven twelve thirteen"},' \
           ' {"type": "gossip", "text": "dropped: unknown type"}, {"type": "recap", "text": "  "}, {"type": "notice", "text": "b"},' \
           ' {"type": "drift", "text": "c"}, {"type": "recap", "text": "d"}]}\n```'
    hints = parse_hints(text)
    assert [h.type for h in hints] == ["next_q", "notice", "drift"]  # at most three, the bad ones skipped
    assert hints[0].text == "one two three four five six seven eight nine ten eleven twelve…"
    long_sentence = '{"hints": [{"type": "notice", "text": "They said fear of saying no to clients is the real enemy. Really."}]}'
    assert parse_hints(long_sentence)[0].text == "They said fear of saying no to clients is the real enemy…"
    assert parse_hints('{"hints": []}') == []  # "no hint" is an answer
    for bad in ("Sure! Ask them about it.", '{"tips": []}', '{"hints": [}'):
        with pytest.raises(HintFormatError):
            parse_hints(bad)


def test_clock_state_measures_drift() -> None:
    run = {"sections": [{"id": "s1", "name": "Intro", "minutes": 10, "planned_start": 0},
                        {"id": "s2", "name": "Ideas", "minutes": 20, "planned_start": 10}]}
    start = 1_000_000
    minute = 60_000
    cur = {"section_id": "s2"}
    # entered "Ideas" 4 min late, 25 min in it (5 over): 9 min behind
    text = clock_state(run, cur, start, {"s2": start + 14 * minute}, 120, start + 39 * minute)
    assert text == 'Session clock: 39 of 120 min. Section "Ideas": 25 of 20 min planned. 9 min behind the plan.'
    assert clock_state(run, cur, None, {}, 120, start) == "The session clock has not been started."
    assert clock_state(run, cur, start, {"s2": start + 8 * minute}, 120, start + 9 * minute).endswith("2 min ahead of the plan.")


def test_prompt_carries_the_plan_and_the_recent_lines_only_as_given() -> None:
    cur = {"title": "Pick an idea", "kind": "activity", "type_label": "Word cloud", "question": "Which idea?", "notes": "Push for one."}
    lines = [Line("them", "I like the cheap one", 1000.0), Line("me", "Why that one?", 1030.0)]
    text = user_prompt("Synthetic workshop", cur, None, "On time.", lines, now=1090.0)
    assert "On stage now: Pick an idea (Word cloud)" in text and "Notes: Push for one." in text
    assert "Next: nothing (the end of the session)." in text
    assert "[-01:30] them: I like the cheap one\n[-01:00] me: Why that one?" in text
    assert "Spanish" in system_prompt("es") and "12 words" in system_prompt("en")


def test_chain_falls_back_then_stops_on_a_dead_hub() -> None:
    asked: list[str] = []

    def complete_fn(model: str, messages: list, timeout: float) -> str:
        asked.append(model)
        if model == "claude_haiku":
            raise HubTimeout("slow")
        if model == "gemini_flash_lite":
            return "not json"
        return '{"hints": [{"type": "recap", "text": "Sum up the two options"}]}'

    answer = ask_chain(complete_fn, ["claude_haiku", "gemini_flash_lite", "agentic_light"], [], 20)
    assert asked == ["claude_haiku", "gemini_flash_lite", "agentic_light"]
    assert answer.model == "agentic_light" and answer.hints[0].text == "Sum up the two options" and not answer.failure

    asked.clear()

    def down(model: str, messages: list, timeout: float) -> str:
        asked.append(model)
        raise HubDown("refused")

    answer = ask_chain(down, ["claude_haiku", "gemini_flash_lite"], [], 20)
    assert asked == ["claude_haiku"] and answer.failure == "hub_down"


def test_chain_keeps_to_one_time_budget() -> None:
    now = [0.0]

    def slow(model: str, messages: list, timeout: float) -> str:
        now[0] += timeout  # each model uses all the time it is given
        raise HubTimeout(f"{model} silent")

    answer = ask_chain(slow, ["a", "b", "c"], [], 20, clock=lambda: now[0])
    assert answer.failure == "timeout" and "a silent" in answer.detail and now[0] == 20


def test_transcribe_sends_one_chunk_with_model_and_language(hub: FakeHub) -> None:
    hub.transcripts.append("Probemos la idea más barata.")
    out = transcribe(hub.url, tone(1), model="whisper", language="es", timeout=5)
    assert out["text"] == "Probemos la idea más barata."
    call = hub.calls_to("/v1/audio/transcriptions")[0]
    assert (call["model"], call["language"], call["response_format"]) == ("whisper", "es", "verbose_json")
    assert call["bytes"] > 32000  # the chunk's WAV, not the transcript so far


def test_hub_failures_are_told_apart(hub: FakeHub) -> None:
    hub.failing.add("claude_haiku")
    with pytest.raises(HubError):
        complete(hub.url, "claude_haiku", [], timeout=5)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    with pytest.raises(HubDown):
        complete(f"http://127.0.0.1:{closed}", "claude_haiku", [], timeout=5)
    hub.delay_s = 1.5
    with pytest.raises(HubTimeout):
        complete(hub.url, "gemini_flash_lite", [], timeout=0.5)
