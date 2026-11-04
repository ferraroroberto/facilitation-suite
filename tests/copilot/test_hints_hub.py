"""Hints: the strict JSON, the clock sentence, the model chain; and the hub client's three failures."""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from src.copilot.hints import (
    MODEL_HINT_TYPES,
    HintFormatError,
    Line,
    ask_chain,
    clock_state,
    drift_hint,
    drift_now,
    parse_hints,
    speaker_labels,
    system_prompt,
    user_prompt,
)
from src.copilot.hub_client import HubDown, HubError, HubTimeout, complete, transcribe
from src.copilot.summary import RateLimit, parse_summary, summary_messages
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


def test_interview_mode_names_the_interviewee_and_leans_to_follow_ups() -> None:
    lines = [Line("me", "What slowed the last release?", 1000.0), Line("them", "Late changes, mostly", 1030.0)]
    labels = speaker_labels("interview", "Alex Doe")
    system, text = system_prompt("en", "interview", "Alex Doe"), user_prompt("Synthetic call", None, None, "On time.", lines, 1090.0, labels=labels)
    assert "one-to-one interview" in system and "'Alex Doe' is the interviewee" in system and "Favour next_q and notice" in system
    assert "[-01:30] me: What slowed the last release?\n[-01:00] Alex Doe: Late changes, mostly" in text
    assert "'interviewee' is the person being interviewed" in system_prompt("en", "interview", "")  # no name yet
    assert speaker_labels("interview", "")["them"] == "interviewee" and "drift" not in system
    summary = summary_messages("en", "", lines, 1090.0, "interview", "Alex Doe")
    assert "'Alex Doe' is the interviewee" in summary[0]["content"] and "Alex Doe: Late changes" in summary[1]["content"]
    # the workshop prompts are as they were, whatever name is saved
    assert system_prompt("en", "workshop", "Alex Doe") == system_prompt("en")
    assert "'them' is everyone else on the call" in system_prompt("en")
    assert summary_messages("en", "", lines, 1090.0, "workshop", "Alex Doe") == summary_messages("en", "", lines, 1090.0)
    assert "them: Late changes" in summary_messages("en", "", lines, 1090.0)[1]["content"]


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


def test_drift_is_computed_never_taken_from_a_model() -> None:
    answer = '{"hints": [{"type": "drift", "text": "You are late"}, {"type": "recap", "text": "Sum up"}]}'
    assert [h.type for h in parse_hints(answer, MODEL_HINT_TYPES)] == ["recap"]
    assert "drift" not in system_prompt("en")
    run = {"sections": [{"id": "s1", "name": "Intro", "minutes": 10, "planned_start": 0}]}
    start, minute = 1_000_000, 60_000
    assert drift_now(run, {"section_id": "s1"}, start, {"s1": start}, 60, start + 17 * minute) == ("s1", "Intro", 7)
    assert drift_now(run, {"section_id": "s1"}, start, {"s1": start}, 60, start + 9 * minute) is None
    assert drift_now({}, {}, start, {}, 60, start + 66 * minute) == ("session", "", 6)  # outside sections: past the length
    assert drift_now(run, {"section_id": "s1"}, None, {}, 60, start) is None
    assert drift_hint("es", "Intro", 7).text == "7 min de retraso en Intro"
    assert drift_hint("en", "", 6).text == "6 min behind the plan"


def test_the_summary_is_trimmed_and_the_cap_slides_over_the_hour() -> None:
    assert parse_summary("  They chose   the freeze. ") == "They chose the freeze."
    assert parse_summary(" ".join(["word"] * 400)).endswith("word…") and len(parse_summary(" ".join(["w"] * 400)).split()) == 300
    with pytest.raises(HintFormatError):
        parse_summary("   ")
    now = [0.0]
    limit = RateLimit(clock=lambda: now[0])
    assert limit.take(2) and limit.take(2) and not limit.take(2)
    assert limit.retry_in() == 3600
    limit.refund()
    assert limit.take(2)
    now[0] = 3600.5
    assert not limit.full(2) and limit.take(0)  # the window slid; 0 = no cap
