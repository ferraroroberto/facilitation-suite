"""VAD chunking on synthetic WAVs, and the WAV kept on disk as it arrives."""

from __future__ import annotations

import builtins
import os
import wave
from pathlib import Path

import pytest

from src.copilot.audio import FLUSH_BYTES, WavRecorder, read_pcm, wav_bytes, write_wav
from src.copilot.vad import SAMPLE_RATE, Chunker, frame_dbfs
from tests.copilot.synth import silence, speech, tone


def feed_all(chunker: Chunker, pcm: bytes, block: int = 3200) -> list:
    out = []
    for at in range(0, len(pcm), block):  # 100 ms blocks, as the devices deliver them
        out += chunker.feed(pcm[at:at + block])
    return out


def test_frame_level_in_dbfs() -> None:
    assert abs(frame_dbfs(tone(0.02, dbfs=-20)) - (-23.0)) < 0.5  # a sine's RMS sits 3 dB under its peak
    assert frame_dbfs(b"\x00\x00" * 320) == -120.0
    assert frame_dbfs(silence(0.02)) < -70


def test_cut_at_the_first_pause_after_the_minimum() -> None:
    chunker = Chunker("them", min_s=20, max_s=45, silence_ms=600)
    pcm = speech([("voice", 12), ("pause", 0.8), ("voice", 10), ("pause", 0.7), ("voice", 5)])
    chunks = feed_all(chunker, pcm)
    # the 0.8 s pause comes before 20 s, so no cut there; the cut is 600 ms into the pause after 20 s
    assert len(chunks) == 1
    assert chunks[0].speaker == "them" and chunks[0].t0 == 0
    assert 23.3 <= chunks[0].t1 <= 23.5
    rest = chunker.flush()
    assert len(rest) == 1 and rest[0].t0 == chunks[0].t1 and abs(rest[0].t1 - len(pcm) / 2 / SAMPLE_RATE) < 0.01


def test_hard_cap_cuts_a_monologue() -> None:
    chunks = feed_all(Chunker("me", min_s=20, max_s=45), tone(100))
    assert [(c.t0, c.t1) for c in chunks] == [(0, 45), (45, 90)]


def test_silent_and_click_only_chunks_never_reach_whisper() -> None:
    chunker = Chunker("them", min_s=20, max_s=45)
    assert feed_all(chunker, silence(50)) == []  # 45 s of room-tone, cut by the cap, dropped
    assert feed_all(chunker, speech([("pause", 10), ("voice", 0.1), ("pause", 40)])) == []  # a 100 ms click
    assert chunker.flush() == []


def test_wav_bytes_round_trip(tmp_path: Path) -> None:
    pcm = tone(0.5)
    path = tmp_path / "x.wav"
    path.write_bytes(wav_bytes(pcm))
    assert read_pcm(path) == pcm
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 8000)


def test_recorder_appends_and_keeps_the_header_right(tmp_path: Path) -> None:
    rec = WavRecorder(tmp_path / "copilot" / "them-120000.wav", failed=lambda *_: None, written=lambda *_: None)
    pcm = tone(5)
    for at in range(0, len(pcm), 3200):
        rec.write(pcm[at:at + 3200])
    assert (tmp_path / "copilot" / "them-120000.wav").stat().st_size >= FLUSH_BYTES  # flushed while recording
    rec.close()
    assert read_pcm(tmp_path / "copilot" / "them-120000.wav") == pcm


def test_recorder_keeps_samples_through_a_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failed, written = [], []
    path = tmp_path / "me.wav"
    rec = WavRecorder(path, failed=lambda name, exc: failed.append(name), written=written.append)
    real_open = builtins.open
    armed = [True]

    def flaky(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if armed and os.fspath(file) == os.fspath(path):
            armed.clear()
            raise PermissionError(13, "The file is in use by another process", str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky)
    first, second = tone(2), tone(2, hz=330)
    rec.write(first)  # this flush fails: OneDrive holding the file
    assert failed == ["me.wav"] and not path.exists()
    rec.write(second)
    rec.close()
    assert written == ["me.wav"] and read_pcm(path) == first + second


def test_recorder_starts_a_fresh_file_when_the_folder_was_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "live" / "copilot" / "me.wav"
    rec = WavRecorder(path, failed=lambda *_: None, written=lambda *_: None)
    rec.write(tone(2))
    (tmp_path / "live").rename(tmp_path / "live-20261101-120000")  # the presenter's "start over"
    rec.write(tone(2, hz=330))
    rec.close()
    assert read_pcm(path) == tone(2, hz=330)
    assert read_pcm(tmp_path / "live-20261101-120000" / "copilot" / "me.wav") == tone(2)


def test_write_wav_is_what_the_feed_reads(tmp_path: Path) -> None:
    write_wav(tmp_path / "them.wav", tone(1))
    assert read_pcm(tmp_path / "them.wav") == tone(1)
    (tmp_path / "stereo.wav").write_bytes(b"RIFF")
    with pytest.raises((ValueError, EOFError, wave.Error)):
        read_pcm(tmp_path / "stereo.wav")
