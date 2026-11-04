"""The after-call transcript from the podcast recorder's per-side takes: finding the takes, transcribing each
through a fake hub, the lines in time order with the right participant, the one summary of the call, the
failures kept apart, and the live transcript left as it was. Synthetic tracks only (``tests/fixtures/recorder.py``)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import CopilotConfig
from src.copilot.after_call import RECORDED_SUMMARY_FILE, RECORDED_TRANSCRIPT_FILE, AfterCall, AfterCallError
from src.copilot.recorder_tracks import Track, find_tracks, live_window, pcm_blocks
from src.no_window import NO_WINDOW
from src.sessions.model import COPILOT_CONSENT, Session, parse_session
from tests.conftest import SAMPLE_CONFIG, write_test_config
from tests.fixtures.conversation import write_demo_conversation
from tests.fixtures.demo import PLAN, RUN_START_MS, build_demo_session, write_demo_run
from tests.fixtures.fake_hub import FakeHub
from tests.fixtures.recorder import take_name, write_episode, write_take

START = RUN_START_MS / 1000
HOST_ANSWER = {"text": "Welcome to the show. What did you test first?", "segments": [
    {"start": 0.0, "end": 2.0, "text": " Welcome to the show."},
    {"start": 6.0, "end": 9.0, "text": " What did you test first?"},
    {"start": 9.0, "end": 9.5, "text": " [BLANK_AUDIO]"},  # whisper's "nothing here": not a line
]}
GUEST_ANSWER = {"text": "Thanks for having me.", "segments": [{"start": 2.0, "end": 4.0, "text": " Thanks for having me."}]}


@pytest.fixture
def fake() -> Iterator[FakeHub]:
    hub = FakeHub()
    yield hub
    hub.close()


def _session(mode: str = "interview", consent: bool = True) -> Session:
    session = parse_session(PLAN)
    session.copilot_mode = mode
    session.copilot_interviewee = "Dana" if mode == "interview" else ""
    if consent:
        session.checklist[COPILOT_CONSENT] = True
    return session


def _wait(runs: AfterCall, sid: str, timeout_s: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = runs.job(sid)
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"the after-call run did not end within {timeout_s} s: {runs.job(sid)}")


def _folder(tmp_path: Path) -> Path:
    """A finished session folder: the demo run's events and its live copilot logs."""
    folder = tmp_path / "session"
    folder.mkdir(parents=True)
    write_demo_run(folder, pngs=False)
    write_demo_conversation(folder)
    return folder


def _start(tmp_path: Path, fake: FakeHub, session: Session, folder: Path | None = None,
           **cfg: object) -> tuple[AfterCall, Path, dict]:
    folder = folder or _folder(tmp_path)
    episode = tmp_path / "episode"
    write_episode(episode, START + 30)
    session.copilot_recorder_folder = str(episode)
    runs = AfterCall(lambda: CopilotConfig(hub_url=fake.url, **cfg))  # type: ignore[arg-type]
    runs.start("s1", folder, session)
    return runs, folder, _wait(runs, "s1")


# ---------------------------------------------------------------- finding the takes


def test_only_the_main_takes_are_found_oldest_first_and_inside_the_live_window(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    write_episode(episode, START + 30)
    write_take(episode, "guest", START - 86400)  # a take from the day before
    (episode / "video editing" / "recorder - host - broken.json").write_text("{not json", encoding="utf-8")
    found = find_tracks(episode, (START, START + 3600))
    assert [(t.side, t.speaker, t.started) for t in found] == [("host", "me", START + 30), ("guest", "them", START + 31)]
    assert len(find_tracks(episode)) == 3  # no window: every main take
    assert find_tracks(episode / "video editing") == find_tracks(episode)  # the folder itself works too


def test_the_live_window_runs_from_going_live_to_the_last_event() -> None:
    events = [{"at": "2026-10-27T17:00:00+00:00", "event": "session_live"},
              {"at": "2026-10-27T17:20:00+00:00", "event": "item"}, {"at": "bad", "event": "item"}]
    assert live_window(events) == (START, START + 1200)
    assert live_window([{"at": "2026-10-27T17:00:00+00:00", "event": "item"}]) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_an_mp4_take_is_read_through_ffmpeg(tmp_path: Path) -> None:
    wav = write_take(tmp_path, "host", START)
    mp4 = wav.with_name(take_name("host", START, ext="mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "aac", str(mp4)], check=True,
                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    pcm = b"".join(pcm_blocks(Track(mp4, "host", START, 10.0)))
    assert abs(len(pcm) / 32000 - 10.0) < 0.2  # 16 kHz mono 16-bit, about the take's ten seconds


# ---------------------------------------------------------------- the run


def test_each_side_is_transcribed_on_its_own_and_the_lines_are_ordered_by_time(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    folder = _folder(tmp_path)
    live_before = (folder / "live" / "copilot" / "transcript.jsonl").read_bytes()
    runs, folder, job = _start(tmp_path, fake, _session(), folder)

    assert job["state"] == "done" and job["lines"] == 3 and not job["missed"] and not job["summary_failure"]
    lines = [json.loads(ln) for ln in (folder / "live" / "copilot" / RECORDED_TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()]
    host0, guest0 = int((START + 30) * 1000), int((START + 31) * 1000)
    assert [(ln["speaker"], ln["side"], ln["name"], ln["text"], ln["t0"]) for ln in lines] == [
        ("me", "host", "", "Welcome to the show.", host0),
        ("them", "guest", "Dana", "Thanks for having me.", guest0 + 2000),
        ("me", "host", "", "What did you test first?", host0 + 6000),
    ]
    assert lines[0]["track"].startswith("recorder - host - ")
    calls = fake.calls_to("/v1/audio/transcriptions")
    assert len(calls) == 2 and all(c["language"] == "en" and c["model"] == "whisper" for c in calls)
    # The live transcript is never written by the pass.
    assert (folder / "live" / "copilot" / "transcript.jsonl").read_bytes() == live_before


def test_the_summary_reads_the_whole_recorded_transcript_with_the_interview_prompt(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    _, folder, _ = _start(tmp_path, fake, _session())
    summary = json.loads((folder / "live" / "copilot" / RECORDED_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["text"] == fake.summary and summary["lines"] == 3 and summary["model"] == "claude_haiku"
    (call,) = fake.summary_calls()
    assert "one-to-one interview" in call["messages"][0]["content"]
    assert "Dana: Thanks for having me." in call["messages"][1]["content"]
    assert "me: What did you test first?" in call["messages"][1]["content"]


def test_a_workshop_session_gets_the_workshop_summary_prompt(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    _start(tmp_path, fake, _session(mode="workshop"))
    (call,) = fake.summary_calls()
    assert "everyone else on the call" in call["messages"][0]["content"]
    assert "them: Thanks for having me." in call["messages"][1]["content"]


def test_a_failed_chunk_is_missed_and_the_run_goes_on(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [500, GUEST_ANSWER]
    _, folder, job = _start(tmp_path, fake, _session())
    assert job["state"] == "done" and job["missed"] == 1 and job["missed_detail"].startswith("error:")
    lines = (folder / "live" / "copilot" / RECORDED_TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["side"] for ln in lines] == ["guest"]


def test_the_hub_down_writes_no_transcript(tmp_path: Path, fake: FakeHub) -> None:
    url = fake.url
    fake.close()
    folder = _folder(tmp_path)
    write_episode(tmp_path / "episode", START + 30)
    session = _session()
    session.copilot_recorder_folder = str(tmp_path / "episode")
    runs = AfterCall(lambda: CopilotConfig(hub_url=url))
    runs.start("s1", folder, session)
    job = _wait(runs, "s1")
    assert (job["state"], job["failure"]) == ("failed", "hub_down")
    assert not (folder / "live" / "copilot" / RECORDED_TRANSCRIPT_FILE).exists()
    assert not (folder / "live" / "copilot" / RECORDED_SUMMARY_FILE).exists()


def test_a_summary_that_fails_has_its_own_state_and_the_transcript_stands(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    fake.failing = {"claude_haiku", "gemini_flash_lite", "agentic_light"}
    runs, folder, job = _start(tmp_path, fake, _session())
    copilot = folder / "live" / "copilot"
    assert (job["state"], job["failure"], job["summary_failure"]) == ("done", "", "error")
    assert (copilot / RECORDED_TRANSCRIPT_FILE).is_file()
    assert json.loads((copilot / RECORDED_SUMMARY_FILE).read_text(encoding="utf-8"))["failure"] == "error"

    fake.failing = set()
    session = _session()
    runs.start_summary("s1", folder, session)
    assert _wait(runs, "s1")["summary_failure"] == ""
    assert json.loads((copilot / RECORDED_SUMMARY_FILE).read_text(encoding="utf-8"))["text"] == fake.summary

    # Asked again and failing, the summary already there is kept.
    fake.failing = {"claude_haiku", "gemini_flash_lite", "agentic_light"}
    runs.start_summary("s1", folder, session)
    assert _wait(runs, "s1")["summary_failure"] == "error"
    assert json.loads((copilot / RECORDED_SUMMARY_FILE).read_text(encoding="utf-8"))["text"] == fake.summary


def test_a_local_only_session_summarises_with_the_local_model_only(tmp_path: Path, fake: FakeHub) -> None:
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    session = _session()
    session.copilot_local_only = True
    _start(tmp_path, fake, session)
    assert [c["model"] for c in fake.summary_calls()] == ["agentic_light"]


@pytest.mark.parametrize(("change", "code"), [
    (lambda s, p: s.checklist.clear(), "consent_required"),
    (lambda s, p: setattr(s, "copilot_recorder_folder", ""), "no_folder"),
    (lambda s, p: setattr(s, "copilot_recorder_folder", str(p / "nowhere")), "folder_missing"),
    (lambda s, p: setattr(s, "copilot_recorder_folder", str(p)), "no_tracks"),
])
def test_a_run_that_cannot_start_says_why(tmp_path: Path, fake: FakeHub, change, code: str) -> None:  # noqa: ANN001
    session = _session()
    session.copilot_recorder_folder = str(tmp_path / "episode")
    write_episode(tmp_path / "episode", START + 30)
    change(session, tmp_path)
    with pytest.raises(AfterCallError) as err:
        AfterCall(lambda: CopilotConfig(hub_url=fake.url)).start("s1", _folder(tmp_path / "s"), session)
    assert err.value.code == code


# ---------------------------------------------------------------- through the app


@pytest.fixture
def app(isolated_env: Path, fake: FakeHub):
    copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
    copilot.update(hub_url=fake.url, capture="off")
    copilot["overlay"]["enabled"] = False
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), copilot=copilot)
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "recorded", isolated_env / "sessions.local.yaml")
    write_demo_run(folder, pngs=False)
    write_demo_conversation(folder)
    from app.webapp.server import create_app

    application = create_app()
    application.state.demo = (sid, folder)
    return application


def test_the_card_reads_the_recorded_transcript_first_and_the_live_one_on_request(app, fake: FakeHub, isolated_env: Path) -> None:
    sid, folder = app.state.demo
    episode = isolated_env / "episode"
    write_episode(episode, START + 30)
    fake.transcripts = [HOST_ANSWER, GUEST_ANSWER]
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        before = pc.get(f"/api/sessions/{sid}/results/conversation").json()
        assert (before["source"], before["sources"], before["summary"]) == ("live", ["live"], None)
        assert pc.get(f"/api/sessions/{sid}/results/recorded").json()["consent"] is False

        r = pc.post(f"/api/sessions/{sid}/results/recorded", json={"folder": str(episode)})
        assert r.status_code == 409 and r.json()["error"]["code"] == "consent_required"
        app.state.store.edit(sid, lambda s: s.checklist.__setitem__(COPILOT_CONSENT, True))
        r = pc.post(f"/api/sessions/{sid}/results/recorded", json={"folder": str(episode)})
        assert r.status_code == 200 and r.json()["job"]["state"] == "running"
        assert r.json()["folder"] == str(episode)
        job = _wait(app.state.after_call, sid)
        assert job["state"] == "done"
        status = pc.get(f"/api/sessions/{sid}/results/recorded").json()
        assert status["transcript"]["lines"] == 3 and status["summary"]["text"] == fake.summary

        recorded = pc.get(f"/api/sessions/{sid}/results/conversation").json()
        assert (recorded["source"], recorded["sources"]) == ("recorded", ["recorded", "live"])
        assert recorded["lines"] == 3 and recorded["summary"]["text"] == fake.summary
        texts = [e["text"] for s in recorded["segments"] for e in s["entries"] if e["kind"] == "line"]
        assert texts == ["Welcome to the show.", "Thanks for having me.", "What did you test first?"]

        live = pc.get(f"/api/sessions/{sid}/results/conversation?source=live").json()
        assert live["source"] == "live" and live["lines"] == before["lines"] and live["summary"] is None
        assert live["segments"] == before["segments"]
        assert pc.post(f"/api/sessions/{sid}/results/recorded/summary").status_code == 200
        _wait(app.state.after_call, sid)
    # The folder is the Results tab's: a Plan-tab save from an older copy keeps it.
    stale = parse_session(PLAN)
    app.state.store.save_plan(sid, stale)
    assert app.state.store.load(sid).copilot_recorder_folder == str(episode)


def test_a_session_without_tracks_reads_as_before(app) -> None:
    sid, folder = app.state.demo
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        data = pc.get(f"/api/sessions/{sid}/results/conversation").json()
        assert data["source"] == "live" and data["sources"] == ["live"] and data["lines"] == 5
        status = pc.get(f"/api/sessions/{sid}/results/recorded").json()
        assert (status["job"], status["transcript"], status["summary"]) == (None, None, None)
        r = pc.post(f"/api/sessions/{sid}/results/recorded/summary")
        assert r.status_code == 409  # neither consent nor a transcript to summarise
    assert not (folder / "live" / "copilot" / RECORDED_TRANSCRIPT_FILE).exists()


def test_reading_the_tracks_is_refused_from_another_device(app, isolated_env: Path) -> None:
    sid, _ = app.state.demo
    from tests.conftest import paired_remote

    r = paired_remote(app).post(f"/api/sessions/{sid}/results/recorded", json={"folder": str(isolated_env)})
    assert r.status_code == 403 and r.json()["error"]["code"] == "local_only"
