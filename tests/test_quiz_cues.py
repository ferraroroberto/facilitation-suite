"""The quiz's sound cues (#53): phase changes play session-folder files through MusicService (fake backend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import load_config
from src.live.actions import run_action
from src.live.hub import LiveHub
from src.music.library import cue_file
from src.music.service import MusicService
from src.quiz.cues import QuizCues
from src.quiz.service import QuizService
from src.sessions.store import SESSION_FILE, SessionStore
from tests.fixtures.demo import build_demo_session
from tests.test_music import FakeBackend, write_wav
from tests.test_quiz_engine import QUIZ_SECTION

AD_HOC = {"source": "file", "path": "audio/song.wav", "volume": 60, "fade_in_s": 1, "fade_out_s": 1, "start": "on_enter",
          "on_leave": "keep_playing"}


@pytest.fixture
def quiz_folder(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "cues", isolated_env / "sessions.local.yaml")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    raw["sections"].insert(1, QUIZ_SECTION)
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    write_wav(folder / "audio" / "song.wav")
    return sid, folder


def rig(sid: str) -> tuple[LiveHub, QuizService, MusicService, FakeBackend]:
    hub = LiveHub(SessionStore(load_config()))
    made: list[FakeBackend] = []
    music = MusicService(hub, {"file": lambda sink: made.append(FakeBackend(sink)) or made[-1]})
    quiz = QuizService(hub)
    QuizCues(hub, quiz, music)
    hub.activate(sid)
    return hub, quiz, music, made[0]


def goto_id(hub: LiveHub, item_id: str) -> None:
    hub.goto(hub.item_by_id(item_id)["index"])


def plays(fake: FakeBackend) -> list[str]:
    return [c[1] for c in fake.calls if c[0] == "play"]


def cues(folder: Path, *names: str, ext: str = ".wav") -> None:
    for name in names:
        write_wav(folder / "audio" / f"quiz-{name}{ext}")


def test_cue_files_are_found_by_name_in_any_playable_format(tmp_path: Path) -> None:
    assert cue_file(tmp_path, "quiz-lobby") is None  # no audio/ at all
    write_wav(tmp_path / "audio" / "Quiz-Lobby.OGG")  # the name matches without regard to case
    (tmp_path / "audio" / "quiz-reveal.m4a").write_bytes(b"not playable")
    write_wav(tmp_path / "audio" / "quiz-reveal.flac")
    write_wav(tmp_path / "audio" / "quiz-reveal.mp3")
    assert cue_file(tmp_path, "quiz-lobby").name == "Quiz-Lobby.OGG"
    assert cue_file(tmp_path, "quiz-reveal").name == "quiz-reveal.mp3"  # mp3 first, then wav, ogg, flac
    assert cue_file(tmp_path, "quiz-podium") is None


def test_without_cue_files_the_quiz_is_silent(quiz_folder: tuple[str, Path]) -> None:
    hub, quiz, music, fake = rig(quiz_folder[0])
    goto_id(hub, "qz-lobby")
    quiz.join("Ana")
    for _ in range(3):
        run_action(hub, "next")  # question, reveal, leaderboard
    goto_id(hub, "qz-podium")
    goto_id(hub, "qz-orphan")
    assert fake.calls == [] and music.state == "idle" and music.detail == ""


def test_each_phase_plays_its_cue_and_the_lobby_loops(quiz_folder: tuple[str, Path]) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby", "countdown", "reveal", "podium")
    hub, quiz, music, fake = rig(sid)
    goto_id(hub, "qz-lobby")
    assert plays(fake) == ["quiz-lobby.wav"] and music.track.loop and music.cue_name == "quiz-lobby"
    assert hub.snapshot()["state"]["music"]["cue"] == "quiz-lobby"
    quiz.join("Ana")
    quiz.join("Bo")  # joins change nothing: the loop is not started over
    assert plays(fake) == ["quiz-lobby.wav"]

    run_action(hub, "next")  # question: the countdown crossfades over the lobby loop
    assert plays(fake)[-1] == "quiz-countdown.wav" and not music.track.loop
    run_action(hub, "next")  # reveal
    assert plays(fake)[-1] == "quiz-reveal.wav"
    run_action(hub, "next")  # leaderboard: no cue of its own — the reveal's fades out
    assert fake.calls[-1][0] == "stop" and music.state == "idle" and music.cue_name is None
    goto_id(hub, "qz-podium")  # two players: 2nd, then 1st — the fanfare waits for 1st (#89)
    assert plays(fake)[-1] == "quiz-reveal.wav"
    run_action(hub, "next")  # 2nd
    assert plays(fake)[-1] == "quiz-reveal.wav"
    run_action(hub, "next")  # 1st
    assert plays(fake)[-1] == "quiz-podium.wav"
    goto_id(hub, "slide-101")  # off the quiz: the cue fades out
    assert fake.calls[-1][0] == "stop" and music.state == "idle"
    assert plays(fake) == ["quiz-lobby.wav", "quiz-countdown.wav", "quiz-reveal.wav", "quiz-podium.wav"]


def test_a_phase_without_its_file_stops_the_lobby_loop(quiz_folder: tuple[str, Path]) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby")
    hub, quiz, music, fake = rig(sid)
    goto_id(hub, "qz-lobby")
    run_action(hub, "next")  # no countdown file: silence, never the lobby loop under the question
    assert plays(fake) == ["quiz-lobby.wav"] and fake.calls[-1][0] == "stop" and music.state == "idle"


def test_a_cue_never_stomps_item_or_ad_hoc_music(quiz_folder: tuple[str, Path]) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby", "countdown", "reveal")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    raw["sections"][1]["items"][1]["music"] = AD_HOC  # qz-1 plays its own music on enter, and keeps it on leaving
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    hub, quiz, music, fake = rig(sid)

    goto_id(hub, "qz-lobby")
    goto_id(hub, "qz-1")  # the item's music replaces the lobby loop; its countdown cue is skipped
    assert plays(fake) == ["quiz-lobby.wav", "song.wav"] and music.owner == "qz-1" and music.cue_name is None
    run_action(hub, "next")  # reveal: skipped too, the item's music plays on
    assert plays(fake) == ["quiz-lobby.wav", "song.wav"] and fake.calls[-1][0] == "play" and music.state == "playing"

    run_action(hub, "music_toggle")  # paused by hand: still the item's — a cue does not take over
    goto_id(hub, "qz-2")
    assert plays(fake)[-1] == "song.wav" and music.state == "paused"

    run_action(hub, "music_stop")
    extra = next(t for t in music.tracks() if t["label"] == "song.wav")
    run_action(hub, "music_play", str(extra["n"]))  # ad-hoc music from the presenter
    run_action(hub, "next")  # qz-2's reveal: skipped, the ad-hoc music untouched
    assert plays(fake)[-1] == "song.wav" and music.owner is None and music.cue_name is None and music.state == "playing"


def test_a_cue_is_replaced_by_the_presenters_music(quiz_folder: tuple[str, Path]) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby")
    hub, quiz, music, fake = rig(sid)
    goto_id(hub, "qz-lobby")
    extra = next(t for t in music.tracks() if t["label"] == "song.wav")
    run_action(hub, "music_play", str(extra["n"]))  # the latest command wins over a cue
    assert music.cue_name is None and music.owner is None
    music.end_cue()  # only ever a cue: the presenter's music plays on
    assert fake.calls[-1][0] == "play" and music.state == "playing"


def test_going_live_again_replays_the_lobby_loop(quiz_folder: tuple[str, Path]) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby")
    hub, quiz, music, fake = rig(sid)
    goto_id(hub, "qz-lobby")
    hub.deactivate()
    assert fake.calls[-1][0] == "stop"
    hub.activate(sid)  # resumes on the lobby
    assert plays(fake) == ["quiz-lobby.wav", "quiz-lobby.wav"] and music.cue_name == "quiz-lobby"


def test_a_failing_backend_never_stops_the_game(quiz_folder: tuple[str, Path], caplog: Any) -> None:
    sid, folder = quiz_folder
    cues(folder, "lobby")
    hub = LiveHub(SessionStore(load_config()))
    music = MusicService(hub, {})  # no file backend at all: cue() raises MusicError
    quiz = QuizService(hub)
    QuizCues(hub, quiz, music)
    hub.activate(sid)
    goto_id(hub, "qz-lobby")
    assert quiz.join("Ana").state == "joined"
    run_action(hub, "next")
    assert hub.snapshot()["state"]["quiz"]["phase"] == "question"
    assert "the sound cue failed" in caplog.text
