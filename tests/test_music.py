"""Music: the timer ↔ music state machine (against a fake backend), the file mixer, session audio."""

from __future__ import annotations

import array
import struct
import time
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import load_config
from src.live.actions import run_action
from src.live.hub import LiveHub
from src.music.backend import MusicError, Track
from src.music.file_backend import SAMPLE_RATE, FileBackend, Voice, level
from src.music.library import import_audio, readiness
from src.music.service import FADE_OUT_LONG_S, MusicService
from src.sessions.model import dump_session, parse_session
from src.sessions.offline import OfflineReport
from src.sessions.store import SESSION_FILE, SessionStore
from tests.fixtures.demo import PLAN, build_demo_session


def write_wav(path: Path, seconds: float = 0.2, rate: int = 8000) -> Path:
    """A short synthetic tone (never real music)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", 8000 if (i // 20) % 2 else -8000) for i in range(int(seconds * rate))))
    return path


class FakeBackend:
    """Records every call; the test plays the backend's thread through ``sink``."""

    def __init__(self, sink: Any, kind: str = "file") -> None:
        self.sink, self.kind = sink, kind
        self.calls: list[tuple[Any, ...]] = []

    def play(self, play_id: int, track: Track, volume: int, fade_in_s: float) -> None:
        self.calls.append(("play", track.label, volume, fade_in_s))

    def pause(self, fade_s: float) -> None:
        self.calls.append(("pause", fade_s))

    def resume(self, volume: int, fade_s: float) -> None:
        self.calls.append(("resume", volume, fade_s))

    def stop(self, fade_s: float) -> None:
        self.calls.append(("stop", fade_s))

    def set_volume(self, volume: int) -> None:
        self.calls.append(("volume", volume))

    def fade(self, to: int, seconds: float) -> None:
        self.calls.append(("fade", to, seconds))

    def snapshot(self) -> dict[str, Any]:
        return {"device": "fake"}

    def close(self) -> None:
        self.calls.append(("close",))


MUSIC_A = {"source": "file", "path": "audio/a.wav", "volume": 70, "fade_in_s": 3, "fade_out_s": 4, "start": "with_timer"}
MUSIC_B = {"source": "file", "path": "audio/b.wav", "volume": 50, "fade_in_s": 1, "fade_out_s": 2, "start": "on_enter",
           "on_leave": "keep_playing"}
MUSIC_C = {"source": "file", "path": "audio/c.wav", "start": "manual"}


@pytest.fixture
def music_session(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "music", isolated_env / "sessions.local.yaml")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    for sec in raw["sections"]:
        for it in sec["items"]:
            if it.get("slide_id") == 105:
                it["music"] = MUSIC_A  # a slide with a manual 5-minute timer
            elif it["id"] == "brk-coffee":
                it["music"] = MUSIC_B  # the break: its timer starts on enter
            elif it.get("slide_id") == 106:
                it["music"] = MUSIC_C
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    for name in ("a", "b", "c", "extra"):
        write_wav(folder / "audio" / f"{name}.wav")
    return sid, folder


@pytest.fixture
def rig(music_session: tuple[str, Path]) -> tuple[LiveHub, MusicService, FakeBackend]:
    hub = LiveHub(SessionStore(load_config()))
    made: list[FakeBackend] = []
    music = MusicService(hub, {"file": lambda sink: made.append(FakeBackend(sink)) or made[-1]})
    hub.activate(music_session[0])
    made[0].calls.clear()
    return hub, music, made[0]


def goto_id(hub: LiveHub, item_id: str) -> dict[str, Any]:
    item = hub.item_by_id(item_id)
    hub.goto(item["index"])
    return item


def end_timer(hub: LiveHub, item_id: str) -> None:
    """Reach 00:00 now (the hub has no event loop in unit tests)."""
    hub.timers[item_id].total = 0
    hub._timer_end(item_id)


def test_items_without_music_behave_as_before(isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "plain", isolated_env / "sessions.local.yaml")
    hub = LiveHub(SessionStore(load_config()))
    made: list[FakeBackend] = []
    music = MusicService(hub, {"file": lambda sink: made.append(FakeBackend(sink)) or made[-1]})
    hub.activate(sid)
    assert all(it["music"] is None for it in hub.items)
    goto_id(hub, "slide-105")
    run_action(hub, "timer_toggle")
    run_action(hub, "timer_toggle")
    run_action(hub, "timer_toggle")
    run_action(hub, "timer_reset")
    goto_id(hub, "brk-coffee")
    end_timer(hub, "brk-coffee")
    assert made[0].calls == []
    assert hub.snapshot()["state"]["music"] == {"state": "idle", "detail": "", "owner": None, "cue": None, "volume": 80, "track": None,
                                               "sounding": False, "backend": None, "tracks": [],
                                               "spotify": False}
    assert music.state == "idle"


def test_with_timer_start_pause_resume_reset_and_end(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, fake = rig
    goto_id(hub, "slide-105")
    assert fake.calls == []  # with_timer: nothing on enter
    run_action(hub, "timer_toggle")  # start
    assert fake.calls == [("play", "a.wav", 70, 3.0)] and music.state == "playing" and music.owner == "slide-105"
    run_action(hub, "timer_toggle")  # pause
    assert fake.calls[-1] == ("pause", 4.0) and music.state == "paused"
    run_action(hub, "timer_toggle")  # resume
    assert fake.calls[-1] == ("resume", 70, 3.0) and music.state == "playing"
    run_action(hub, "timer_reset")
    assert fake.calls[-1] == ("stop", 4.0) and music.state == "idle" and music.owner is None
    run_action(hub, "timer_toggle")  # a fresh start plays again from the top
    assert fake.calls[-1] == ("play", "a.wav", 70, 3.0)
    end_timer(hub, "slide-105")  # 00:00
    assert fake.calls[-1] == ("stop", 4.0) and music.state == "idle"
    state = hub.snapshot()["state"]["music"]
    assert [t["label"] for t in state["tracks"]] == ["a.wav", "c.wav", "b.wav", "extra.wav"]
    assert state["tracks"][0]["items"] == ["slide-105"]


def test_leaving_an_item_follows_on_leave(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, fake = rig
    goto_id(hub, "slide-105")
    run_action(hub, "timer_toggle")
    run_action(hub, "next")  # on_leave: fade_out (the default)
    assert fake.calls[-1] == ("stop", 4.0) and music.state == "idle"

    brk = goto_id(hub, "brk-coffee")  # on_enter music, and the break's timer starts on enter too: one play
    assert [c[0] for c in fake.calls].count("play") == 2 and fake.calls[-1] == ("play", "b.wav", 50, 1.0)
    assert music.owner == brk["id"]
    run_action(hub, "next")  # keep_playing
    assert fake.calls[-1][0] == "play" and music.state == "playing" and music.owner == "brk-coffee"
    end_timer(hub, "brk-coffee")  # the break's own timer still ends it, from anywhere
    assert fake.calls[-1] == ("stop", 2.0) and music.state == "idle"


def test_ad_hoc_music_is_not_touched_by_item_timers(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, fake = rig
    with pytest.raises(MusicError) as err:
        run_action(hub, "music_toggle")  # slide 101 has no music, nothing played yet
    assert err.value.code == "no_music"
    extra = next(t for t in music.tracks() if t["label"] == "extra.wav")
    run_action(hub, "music_play", str(extra["n"]))
    assert fake.calls[-1] == ("play", "extra.wav", 80, 2.0) and music.owner is None

    goto_id(hub, "slide-106")  # manual music: entering plays nothing; leaving the item leaves ad-hoc music alone
    goto_id(hub, "slide-104")
    assert fake.calls == [("play", "extra.wav", 80, 2.0)]
    goto_id(hub, "slide-105")
    run_action(hub, "music_toggle")  # pause by hand
    assert fake.calls[-1] == ("pause", 2.0) and music.state == "paused"
    # A timer start of an item with with_timer music: the latest command wins.
    run_action(hub, "timer_toggle")
    assert fake.calls[-1] == ("play", "a.wav", 70, 3.0) and music.owner == "slide-105"
    run_action(hub, "music_fade_out")
    assert fake.calls[-1] == ("stop", FADE_OUT_LONG_S) and music.owner is None
    run_action(hub, "timer_toggle")  # pause the timer: the music is no longer its own
    run_action(hub, "timer_reset")
    assert fake.calls[-1] == ("stop", FADE_OUT_LONG_S)


def test_the_same_track_is_adopted_not_restarted(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, fake = rig
    goto_id(hub, "slide-105")
    run_action(hub, "music_toggle")  # nothing playing: the item's music, by hand
    assert fake.calls == [("play", "a.wav", 70, 3.0)] and music.owner is None
    run_action(hub, "timer_toggle")  # the timer adopts it
    assert fake.calls == [("play", "a.wav", 70, 3.0)] and music.owner == "slide-105"
    run_action(hub, "music_toggle")  # a pause by hand keeps the owner…
    run_action(hub, "timer_toggle")  # …so the timer's pause has nothing to do
    run_action(hub, "timer_toggle")  # and its resume resumes it
    assert fake.calls[1:] == [("pause", 4.0), ("resume", 70, 3.0)]
    run_action(hub, "music_volume", "35")
    assert fake.calls[-1] == ("volume", 35) and hub.snapshot()["state"]["music"]["volume"] == 35
    with pytest.raises(MusicError):
        run_action(hub, "music_volume", "150")
    run_action(hub, "music_stop")
    assert fake.calls[-1] == ("stop", 4.0) and music.state == "idle"
    run_action(hub, "music_stop")  # nothing playing: nothing to do, no error
    assert fake.calls[-1] == ("stop", 4.0) and len(fake.calls) == 5


def test_backend_events_errors_and_missing_files(rig: tuple[LiveHub, MusicService, FakeBackend],
                                                 music_session: tuple[str, Path]) -> None:
    hub, music, fake = rig
    goto_id(hub, "slide-105")
    run_action(hub, "timer_toggle")
    fake.sink("playing", music.play_id, "a.wav")
    assert hub.snapshot()["state"]["music"]["sounding"] is True
    fake.sink("ended", music.play_id - 1, "old")  # a replaced track: ignored
    assert music.state == "playing"
    fake.sink("ended", music.play_id, "a.wav")
    assert music.state == "idle" and music.owner is None
    run_action(hub, "timer_reset")
    fake.sink("error", music.play_id, "No audio output")
    assert hub.snapshot()["state"]["music"]["state"] == "error"

    (music_session[1] / "audio" / "a.wav").unlink()
    run_action(hub, "timer_toggle")  # a missing file never stops the deck: the chip says it
    snap = hub.snapshot()["state"]["music"]
    assert snap["state"] == "error" and "a.wav is not in the session folder" in snap["detail"]
    run_action(hub, "next")
    assert hub.current()["kind"] == "activity"


def test_spotify_without_a_backend_is_an_error_state(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, _ = rig
    item = hub.item_by_id("slide-105")
    item["music"] = dict(item["music"], source="spotify", uri="spotify:playlist:abc")
    hub.goto(item["index"])
    run_action(hub, "timer_toggle")
    assert music.state == "error" and "not set up" in music.detail


def test_reset_and_closing_the_session_stop_the_music(rig: tuple[LiveHub, MusicService, FakeBackend]) -> None:
    hub, music, fake = rig
    goto_id(hub, "slide-105")
    run_action(hub, "timer_toggle")
    hub.reset()
    assert fake.calls[-1] == ("stop", 1.0) and music.state == "idle"
    run_action(hub, "music_play", "2")
    hub.deactivate()
    assert fake.calls[-1] == ("stop", 1.0) and music.state == "idle"


def test_music_round_trips_and_stays_out_when_absent() -> None:
    session = parse_session(dict(PLAN, sections=[{"name": "S", "items": [
        {"kind": "slide", "slide_id": 1, "music": {"path": "audio/x.mp3", "loop": True}},
        {"kind": "slide", "slide_id": 2}]}]))
    items = dump_session(session)["sections"][0]["items"]
    assert items[0]["music"]["path"] == "audio/x.mp3" and items[0]["music"]["start"] == "with_timer"
    assert items[0]["music"]["on_leave"] == "fade_out" and items[0]["music"]["loop"] is True
    assert "music" not in items[1]
    with pytest.raises(ValueError):
        parse_session(dict(PLAN, sections=[{"name": "S", "items": [{"kind": "slide", "music": {"volume": 101}}]}]))


# ---------------------------------------------------------------- session audio


def test_import_audio_copies_into_the_session(tmp_path: Path) -> None:
    folder = tmp_path / "session"
    folder.mkdir()
    src = write_wav(tmp_path / "picked" / "tone.wav")
    first = import_audio(folder, src)
    assert first["path"] == "audio/tone.wav" and (folder / "audio" / "tone.wav").is_file()
    assert import_audio(folder, src)["path"] == "audio/tone.wav"  # the same file again: reused
    other = write_wav(tmp_path / "other" / "tone.wav", seconds=0.3)
    assert import_audio(folder, other)["path"] == "audio/tone-2.wav"  # never replaces another track
    (tmp_path / "song.m4a").write_bytes(b"not decodable here")
    with pytest.raises(MusicError) as err:
        import_audio(folder, tmp_path / "song.m4a")
    assert err.value.code == "unsupported_audio"
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"\x00" * 64)
    with pytest.raises(MusicError):
        import_audio(folder, bad)


def test_audio_route_and_readiness(client, isolated_env: Path, music_session: tuple[str, Path], tmp_path: Path) -> None:
    sid, folder = music_session
    checks = {c["key"]: c for c in client.get(f"/api/sessions/{sid}").json()["readiness"]}
    assert checks["music"]["state"] == "ok" and checks["music"]["detail"].startswith("3 tracks")
    (folder / "audio" / "b.wav").unlink()
    session = parse_session(yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8")))
    check = readiness(folder, session, OfflineReport(cloud_only=["audio/c.wav"]))
    assert check["state"] == "warn" and "missing: audio/b.wav" in check["detail"] and "only in the cloud: audio/c.wav" in check["detail"]

    r = client.post(f"/api/sessions/{sid}/audio", json={"path": str(write_wav(tmp_path / "new.wav"))})
    assert r.status_code == 200 and r.json()["path"] == "audio/new.wav"
    r = client.post(f"/api/sessions/{sid}/audio", json={"path": str(tmp_path / "nope.mp3")})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_a_session_without_music_has_no_music_check(client, isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "plain", isolated_env / "sessions.local.yaml")
    assert "music" not in {c["key"] for c in client.get(f"/api/sessions/{sid}").json()["readiness"]}


def test_music_actions_are_stream_deck_buttons(client) -> None:
    rows = {a["id"]: a for a in client.get("/api/actions").json()["actions"]}
    assert rows["music_toggle"]["stream_deck"] and rows["music_stop"]["stream_deck"] and rows["music_fade_out"]["stream_deck"]
    assert rows["music_volume"]["path"] == "/api/actions/music_volume/{n}"
    assert rows["music_play"]["stream_deck"] is False


def test_a_refused_music_action_keeps_the_socket(client, isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "ws", isolated_env / "sessions.local.yaml")
    client.post("/api/live/activate", json={"session": sid})
    r = client.post("/api/actions/music_toggle")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_music"
    with client.websocket_connect("/ws?role=presenter") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "action", "action": "music_toggle"})
        assert ws.receive_json() == {"type": "error", "code": "no_music",
                                     "message": "No music on this item — pick a track in the presenter's Music card"}
        ws.send_json({"type": "action", "action": "next"})
        while (msg := ws.receive_json())["type"] != "state":
            pass
        assert msg["state"]["index"] == 1


# ------------------------------------------------------------------ file mixer


class Ones:
    """A primed source of ``frames`` stereo frames of full-scale 1.0."""

    def __init__(self, frames: int) -> None:
        self.left = frames

    def send(self, n: int) -> array.array:
        take = min(n, self.left)
        self.left -= take
        return array.array("f", [1.0] * (take * 2))


def test_voice_ramps_per_sample_then_pauses_or_stops() -> None:
    v = Voice(1, Track("file", "x", "x"), lambda ref: Ones(SAMPLE_RATE * 10))
    v.ramp(1.0, 100 / SAMPLE_RATE)  # 100 frames up
    out = array.array("f", bytes(200 * 2 * 4))
    v.mix(out, 200)
    assert 0 < out[0] < out[2] < out[100] and out[198] == pytest.approx(1.0) and out[399] == pytest.approx(1.0)
    v.ramp(0.0, 50 / SAMPLE_RATE, "pause")
    out = array.array("f", bytes(100 * 2 * 4))
    v.mix(out, 100)
    assert v.paused and out[199] == 0.0 and v.frames == 300
    v.mix(out, 100)
    assert v.frames == 300  # paused: the position holds
    v.paused = False
    v.ramp(level(50), 0)
    v.ramp(0.0, 10 / SAMPLE_RATE, "stop")
    v.mix(array.array("f", bytes(100 * 2 * 4)), 100)
    assert v.done and not v.ended


def test_voice_ends_or_loops_at_the_end_of_the_file() -> None:
    once = Voice(1, Track("file", "x", "x"), lambda ref: Ones(150))
    once.ramp(1.0, 0)
    out = array.array("f", bytes(100 * 2 * 4))
    once.mix(out, 100)
    once.mix(out, 100)
    assert not once.done
    once.mix(out, 100)
    assert once.done and once.ended
    opened: list[str] = []
    looping = Voice(2, Track("file", "x", "x", loop=True), lambda ref: opened.append(ref) or Ones(150))
    looping.ramp(1.0, 0)
    for _ in range(5):
        looping.mix(out, 100)
    assert not looping.done and len(opened) == 4 and looping.frames == 500  # no gap at the seam


class FakeDevice:
    def __init__(self) -> None:
        self.gen: Any = None
        self.backend = "fake-out"
        self.closed = False

    def start(self, gen: Any) -> None:
        self.gen = gen

    def close(self) -> None:
        self.closed = True


def wait_for(cond: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_file_backend_opens_the_device_mixes_and_reports() -> None:
    events: list[tuple[str, int, str]] = []
    devices: list[FakeDevice] = []
    backend = FileBackend(lambda *e: events.append(e), device_factory=lambda: devices.append(FakeDevice()) or devices[-1],
                          source_factory=lambda ref: Ones(300))
    backend.play(7, Track("file", "tone.wav", "tone.wav"), 100, 0)
    wait_for(lambda: devices and devices[0].gen is not None)  # the worker opened the output
    gen = devices[0].gen
    assert gen.send(200)[0] == pytest.approx(1.0)
    gen.send(200)
    gen.send(200)  # past the end of the file
    wait_for(lambda: any(e[0] == "ended" for e in events))
    assert events == [("playing", 7, "tone.wav"), ("ended", 7, "tone.wav")]
    assert backend.snapshot()["device"] == "fake-out"
    backend.close()
    assert devices[0].closed


def test_a_duplicated_session_takes_its_music_along(music_session: tuple[str, Path]) -> None:
    store = SessionStore(load_config())
    dup = store.duplicate(music_session[0], "Cohort B", "cohort-b")
    assert (Path(dup.path) / "audio" / "a.wav").is_file()
