"""The copilot's conversation on the Results tab: the transcript and its hints read back in order."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.results.conversation import load_conversation
from src.sessions.model import parse_session
from tests.fixtures.conversation import write_demo_conversation
from tests.fixtures.demo import PLAN, build_demo_session, write_demo_run


def _session(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "convo", isolated_env / "sessions.local.yaml")
    write_demo_run(folder, pngs=False)
    return sid, folder


def _shape(entry: dict) -> tuple:
    if entry["kind"] == "line":
        return ("line", entry["speaker"], entry["text"])
    return ("hint", entry["reason"], entry["status"], [h["text"] for h in entry["hints"]])


def test_the_endpoint_returns_the_lines_and_hints_in_order_under_their_item(client, isolated_env: Path) -> None:
    sid, folder = _session(isolated_env)
    write_demo_conversation(folder)
    data = client.get(f"/api/sessions/{sid}/results/conversation").json()

    assert [(s["item_id"], s["in_plan"]) for s in data["segments"]] == [("act-weather", True), ("act-kryptonite", True)]
    assert "inner weather" in data["segments"][0]["title"]
    assert [_shape(e) for e in data["segments"][0]["entries"]] == [
        ("line", "me", "How is everyone's inner weather this morning?"),
        ("line", "them", "Cloudy with a chance of coffee."),
        ("hint", "item", "shown", ["Ask who picked rain and why"]),
        ("line", "them", "Sunny, the week started well."),
        ("hint", "them", "stale", ["Two people mention coffee"]),  # came back as the next item came on
    ]
    assert [_shape(e) for e in data["segments"][1]["entries"]] == [
        ("line", "me", "What drains your energy at work?"),
        ("hint", "item", "failed", []),
        ("line", "them", "Meetings that could have been an email."),
        ("hint", "drift", "shown", ["Personal readme runs 5 min late"]),
    ]
    failed = data["segments"][1]["entries"][1]
    assert failed["failure"] == "timeout" and "detail" not in failed  # the error text stays in the file and the log
    assert (failed["failed"], failed["stale"]) == (True, False)
    assert [(e["failed"], e["stale"]) for e in data["segments"][0]["entries"] if e["kind"] == "hint"] == [(False, False), (False, True)]
    stamps = [e["at_ms"] for s in data["segments"] for e in s["entries"]]
    assert stamps == sorted(stamps)
    assert (data["lines"], data["hints"], data["stale"], data["failed"]) == (5, 2, 1, 1)


def test_a_line_sits_where_it_was_said_not_where_it_was_transcribed(isolated_env: Path) -> None:
    _, folder = _session(isolated_env)
    written = write_demo_conversation(folder)
    data = load_conversation(folder, parse_session(PLAN))
    first = data["segments"][0]["entries"][0]
    rec = written["lines"][0]
    assert first["at_ms"] < int(datetime.fromisoformat(rec["at"]).timestamp() * 1000)


def test_a_session_without_the_copilot_has_no_conversation(client, isolated_env: Path) -> None:
    sid, _ = _session(isolated_env)
    data = client.get(f"/api/sessions/{sid}/results/conversation").json()
    assert data == {"lines": 0, "hints": 0, "stale": 0, "failed": 0, "segments": [], "mode": "workshop", "interviewee": "",
                    "source": "live", "sources": [], "summary": None}


def test_damaged_records_and_summaries_are_left_out_and_an_item_gone_from_the_plan_keeps_its_hints(isolated_env: Path) -> None:
    _, folder = _session(isolated_env)
    write_demo_conversation(folder)
    copilot = folder / "live" / "copilot"
    with open(copilot / "transcript.jsonl", "a", encoding="utf-8") as fh:
        fh.write("not json\n" + json.dumps({"speaker": "them", "text": "no time"}) + "\n")
    with open(copilot / "hints.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": "2099-01-01T00:00:00.000+00:00", "item_id": "act-gone", "reason": "item",
                             "model": "demo-model", "hints": [{"type": "recap", "text": "Recap the gone item"}]}) + "\n")
        fh.write(json.dumps({"at": "2099-01-01T00:01:00.000+00:00", "reason": "summary", "item_id": None,
                             "model": "demo-model", "lag_ms": 900, "hints": [], "lines": 5, "words": 40}) + "\n")
    data = load_conversation(folder, parse_session(PLAN))
    assert data["lines"] == 5
    assert all(e.get("reason") != "summary" for s in data["segments"] for e in s["entries"])
    last = data["segments"][-1]
    assert (last["item_id"], last["in_plan"], last["title"]) == ("act-gone", False, "")
    assert last["entries"][0]["hints"] == [{"type": "recap", "text": "Recap the gone item"}]
