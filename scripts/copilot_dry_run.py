"""A dry run of the live copilot against the real hub, on a DISPOSABLE instance, with synthetic voices.

    & .\\.venv\\Scripts\\python.exe scripts\\copilot_dry_run.py [--minutes 10] [--item-every 100] [--out report.json]

1. It writes a two-voice workshop conversation with Windows' own speech voices (SAPI, no audio
   device needed, no recording of anyone): ``me.wav`` is the facilitator, ``them.wav`` the
   participants, silent while the other speaks, 16 kHz mono, as long as ``--minutes``.
2. It boots its own server (the e2e suite's ``boot_instance``: temp config, ledger and data dir,
   free ports) with ``copilot.capture: files`` aimed at those two files and ``copilot.hub_url`` at
   the real hub, so the chunks go through the hub's whisper and the hints through its models.
3. It takes the synthetic demo session live, starts the session clock, switches Copilot on and
   moves to the next item every ``--item-every`` seconds while the audio plays in real time.
4. It reads ``transcript.jsonl`` and ``hints.jsonl`` and prints the chunk-to-transcript latency
   (audio chunk ended → its line written) and the transcript-to-hint latency (the line or item
   change that asked → the hints back), with the models that answered, every failure, the model
   calls per kind (hints, summary refreshes), the computed drift hints and the requests a cap held back.

It never touches the live app on 8449 or a real session folder.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.copilot.audio import read_pcm, write_wav  # noqa: E402 — needs the sys.path line above
from src.copilot.vad import SAMPLE_BYTES, SAMPLE_RATE  # noqa: E402
from src.jsonl import read_jsonl  # noqa: E402
from tests.conftest import SAMPLE_CONFIG  # noqa: E402
from tests.e2e.conftest import boot_instance, stop_instance  # noqa: E402
from tests.fixtures.demo import build_demo_session  # noqa: E402

logger = logging.getLogger("copilot_dry_run")

SAPI_16K_MONO = 18  # SpeechAudioFormatType SAFT16kHz16BitMono
# A synthetic team workshop: the facilitator ("me") and the participants ("them"), turn by turn.
CONVERSATION = [
    ("me", "Good morning everyone, thanks for joining. Today we want to agree on how we work together for the next quarter."),
    ("them", "Morning. I hope we get to talk about the meetings, because we have far too many of them."),
    ("me", "We will. First, a quick round: what would make today worth your time?"),
    ("them", "For me, leaving with three concrete agreements we actually keep, not a poster on the wall."),
    ("them", "I would like us to decide who owns the release checklist. Right now nobody does."),
    ("me", "Great, ownership of the checklist. Let me write that down. Anyone else?"),
    ("them", "Honestly I want to understand why the last two releases slipped. Nobody has said it out loud."),
    ("me", "That is a fair point. Let's keep it for the common enemy exercise later."),
    ("them", "Can we also talk about code reviews? Some of them sit for a week before anyone looks at them."),
    ("me", "Let's move to the personal readme. Write three things people should know about working with you."),
    ("them", "Mine says I need written context before a meeting, otherwise I can't contribute much."),
    ("them", "I prefer quick calls to long chat threads. Chat makes me lose track of decisions."),
    ("me", "Interesting, those two pull in different directions. How do we handle both?"),
    ("them", "Maybe every call ends with a short written summary in the channel. That would help me a lot."),
    ("them", "As long as the summary is two lines and not a document, I'm in."),
    ("me", "So a two line summary after every call. Does everyone agree with that?"),
    ("them", "Yes, but who writes it? It always ends up being the same person."),
    ("me", "Good question. Let's park the who and come back to it with the working agreement."),
    ("them", "About working smart: I spend half of Monday answering status questions that are already in the tracker."),
    ("them", "Same here. Maybe the tracker is not where people look. The dashboard is out of date."),
    ("me", "What would it take for the dashboard to be the place people trust?"),
    ("them", "Someone has to update it every Friday. Ten minutes, but it has to be somebody's job."),
    ("me", "Now the working agreement. Let's turn what we heard into rules we can live with."),
    ("them", "Rule one: no meeting without an agenda sent the day before. If there's no agenda, we cancel."),
    ("them", "Rule two: reviews get a first look within one working day, even if it's just a comment."),
    ("them", "I'm worried about rule two. With the support rotation I can't always promise a day."),
    ("me", "So we need an exception for the person on support. What would be fair?"),
    ("them", "The person on support is out of the review pool that week. That seems fair to me."),
    ("me", "Let's look at the common enemy. What really made the last releases slip?"),
    ("them", "Late changes. Product asked for things in the last week and we always said yes."),
    ("them", "And testing started too late. We only tested everything together two days before the release."),
    ("them", "I think we are also afraid to say no to the client. That is the real enemy, not the changes."),
    ("me", "That's an important point. Say more about being afraid to say no."),
    ("them", "If we push back, it feels like we are blocking. So we agree and then work the weekend."),
    ("me", "We have about ten minutes left in this section. What do we need to win?"),
    ("them", "A change freeze one week before the release, agreed with product, written down."),
    ("them", "A test environment that is always up. Half the delay is waiting for it."),
    ("them", "And permission to say no without it being a big deal. Maybe the lead says it, not us."),
    ("me", "So three needs: a change freeze, a stable test environment, and backing to say no."),
    ("them", "Can we vote on which one we tackle first? I would start with the freeze."),
    ("me", "Let's do that. Before we close, how are you leaving today, in one word?"),
    ("them", "Hopeful. Tired, but hopeful."),
    ("them", "Clear. I know what I own now."),
    ("me", "Thank you all. I'll send the two line summary, and yes, I'm writing it this time."),
]


def synth(lines: list[tuple[str, str]], folder: Path) -> list[bytes]:
    """Each line spoken by its speaker's voice, as 16 kHz mono PCM, in order."""
    import win32com.client

    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = {v.GetDescription(): v for v in (voice.GetVoices().Item(i) for i in range(voice.GetVoices().Count))}
    english = [v for name, v in voices.items() if "English" in name] or list(voices.values())
    pick = {"me": english[0], "them": english[-1]}
    out: list[bytes] = []
    for n, (speaker, text) in enumerate(lines):
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = SAPI_16K_MONO
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = fmt
        path = folder / f"line-{n:03d}.wav"
        stream.Open(str(path), 3)  # SSFMCreateForWrite
        voice.Voice = pick[speaker]
        voice.Rate = 0 if speaker == "me" else random.choice((-1, 0, 1))  # a few different-sounding participants
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        out.append(read_pcm(path))
        path.unlink()
    return out


def build_feed(folder: Path, minutes: float) -> float:
    """``me.wav`` and ``them.wav`` side by side; returns their length in seconds."""
    rnd = random.Random(402)
    spoken = synth(CONVERSATION, folder)
    me, them = bytearray(), bytearray()
    target = int(minutes * 60 * SAMPLE_RATE * SAMPLE_BYTES)
    n = 0
    while len(me) < target:
        speaker = CONVERSATION[n % len(CONVERSATION)][0]
        pcm = spoken[n % len(spoken)]
        quiet = b"\x00" * len(pcm)
        me += pcm if speaker == "me" else quiet
        them += pcm if speaker == "them" else quiet
        pause = b"\x00" * (int(rnd.uniform(0.8, 2.6) * SAMPLE_RATE) * SAMPLE_BYTES)
        me += pause
        them += pause
        n += 1
    write_wav(folder / "me.wav", bytes(me[:target]))
    write_wav(folder / "them.wav", bytes(them[:target]))
    return target / SAMPLE_BYTES / SAMPLE_RATE


def call(base: str, method: str, path: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — the disposable instance on loopback
        return json.loads(r.read() or b"{}")


def percentiles(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]  # noqa: E731
    return {"n": len(values), "p50_ms": pick(0.5), "p90_ms": pick(0.9), "max_ms": ordered[-1],
            "mean_ms": round(statistics.mean(values))}


def report(folder: Path) -> dict[str, Any]:
    transcript = read_jsonl(folder / "transcript.jsonl")
    records = read_jsonl(folder / "hints.jsonl")
    hints = [h for h in records if h["reason"] not in ("summary", "drift")]
    summaries = [h for h in records if h["reason"] == "summary"]
    answered = [h for h in hints if not h.get("failure")]
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8")) if (folder / "summary.json").is_file() else {}
    return {
        "lines": {"me": sum(1 for t in transcript if t["speaker"] == "me"), "them": sum(1 for t in transcript if t["speaker"] == "them")},
        "chunk_to_transcript": percentiles([t["lag_ms"] for t in transcript if "lag_ms" in t]),
        "transcript_to_hint": percentiles([h["lag_ms"] for h in answered if h["reason"] == "them"]),
        "item_change_to_hint": percentiles([h["lag_ms"] for h in answered if h["reason"] == "item"]),
        "hint_requests": len(hints), "hints_shown": sum(len(h["hints"]) for h in answered if not h.get("dropped")),
        "dropped_stale": sum(1 for h in hints if h.get("dropped") == "stale"),
        "empty_answers": sum(1 for h in answered if not h["hints"]),
        "models": {m: sum(1 for h in answered if h["model"] == m) for m in sorted({h["model"] for h in answered})},
        "failures": [{"reason": h["reason"], "failure": h["failure"], "detail": h.get("detail", "")[:160]} for h in hints if h.get("failure")],
        "sample_hints": [h["hints"] for h in answered if h["hints"]][:6],
        "model_calls": {"hints": len(hints), "summaries": len(summaries)},
        "summary_refresh": percentiles([h["lag_ms"] for h in summaries if not h.get("failure")]),
        "summary_failures": [h.get("detail", "")[:160] for h in summaries if h.get("failure")],
        "summary_words": summary.get("words", 0),
        "drift_hints": [h["hints"][0]["text"] for h in records if h["reason"] == "drift"],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="fs-copilot-dry-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        feed = root / "feed"
        feed.mkdir()
        seconds = build_feed(feed, args.minutes)
        logger.info("✅ synthetic feed ready: %.0f s per channel in %s", seconds, feed)
        copilot = json.loads(SAMPLE_CONFIG.read_text(encoding="utf-8"))["copilot"]
        copilot.update(hub_url=args.hub, capture="files", feed_dir=str(feed))
        copilot["overlay"]["enabled"] = args.overlay
        proc, inst = boot_instance(root / "instance", copilot=copilot)
        try:
            sid, folder = build_demo_session(root / "instance" / "sessions" / "demo" / "copilot",
                                             root / "instance" / "sessions.local.yaml")
            call(inst.base_url, "POST", "/api/live/activate", {"session": sid})
            call(inst.base_url, "POST", "/api/actions/clock_start")
            call(inst.base_url, "POST", "/api/actions/copilot_toggle")
            began = time.monotonic()
            next_item = began + args.item_every
            while time.monotonic() - began < seconds + args.tail:
                if time.monotonic() >= next_item:
                    call(inst.base_url, "POST", "/api/actions/next")
                    next_item += args.item_every
                time.sleep(1.0)
                state = call(inst.base_url, "GET", "/api/live")["state"]["copilot"]
                if int(time.monotonic() - began) % 60 == 0:
                    logger.info("ℹ️ %3.0f s · %d lines · %d hints · behind %d s · problems %s", time.monotonic() - began,
                                state["stats"]["lines"], state["stats"]["hints"], state["behind_s"],
                                [p["code"] for p in state["problems"]] or "none")
            call(inst.base_url, "POST", "/api/actions/copilot_toggle")
            time.sleep(3)
            out = {"audio_s": round(seconds), "item_every_s": args.item_every, **report(folder / "live" / "copilot"),
                   "capped": state["stats"].get("capped", 0)}
            log = root / "instance" / "data" / "logs" / "overlay.log"
            out["overlay_log"] = log.read_text(encoding="utf-8").splitlines()[-12:] if log.is_file() else []
            return out
        finally:
            stop_instance(proc)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minutes", type=float, default=10.0, help="length of the synthetic conversation")
    p.add_argument("--item-every", type=float, default=100.0, help="seconds between item changes")
    p.add_argument("--tail", type=float, default=40.0, help="seconds to wait after the audio for the last chunks")
    p.add_argument("--hub", default="http://127.0.0.1:8000", help="the local LLM hub")
    p.add_argument("--overlay", action="store_true", help="also run the overlay process")
    p.add_argument("--out", type=Path, help="write the report as JSON here")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    result = run(args)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    logger.info("report:\n%s", text)
    return 0 if result["chunk_to_transcript"].get("n") else 1


if __name__ == "__main__":
    raise SystemExit(main())
