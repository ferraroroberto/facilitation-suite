# Live copilot

During a live online session (a workshop, an interview, an important call), Copilot records your mic and the call's sound, transcribes them in short chunks through the local LLM hub, and has a model read the running transcript together with the session's plan and notes to suggest a few discreet hints: what to ask next, what to notice, that the agenda is slipping, a point worth summing up. The hints show on the presenter and on an overlay window only you see. The recording itself is never hidden.

**Consent.** Recording people needs their informed consent and a visible notice; in Spain and the EU, staying in the call is not consent. Tell the participants before you switch Copilot on. The presenter's Copilot card shows a **Recording** mark the whole time it runs. (Not legal advice.)

## Switching it on

The presenter's **Copilot** card, under the notes, has the switch (also the live action `copilot_toggle`, so a Stream Deck button or the phone remote can flip it). It works only while a session is live and is off whenever a session goes live, closes or is replaced: a restart never starts recording by itself.

The card's **Local only** switch belongs to the session (saved as `copilot_local_only` in its `session.yaml`): hints then come from the local model alone (`copilot.local_model`), for a meeting whose content must not reach a cloud model. Transcription always goes to the hub's whisper.

## What happens while it is on

| Step | What |
|---|---|
| Capture | two channels at 16 kHz mono: **me** = your mic (Windows' default input, or `copilot.mic_device`), **them** = a WASAPI loopback of the call's output (the default output, or `copilot.call_device`): everyone else on the call. Both are written to `live/copilot/me-<time>.wav` and `them-<time>.wav` in the session folder as they arrive |
| Chunks | each channel is cut by its level: the first pause of at least `silence_ms` (600 ms) once a chunk is `min_chunk_s` (20 s) long, and at `max_chunk_s` (45 s) whatever is being said. A chunk with almost no voice (silence, a click) is dropped before it reaches whisper, which invents text on near-silence |
| Transcript | each chunk goes once to the hub (`POST /v1/audio/transcriptions`, `model=whisper` — not the default `transcribe` role, which drops Spanish code-switched tails — in the session's language) and is appended to `live/copilot/transcript.jsonl` as `{t0, t1, speaker, text, lag_ms}`. The transcript is never sent again as a whole |
| Hints | asked on a new **them** line at most once per `hint_interval_s` (45 s), and on every item change. The model reads a stable prefix (the session's title, the rolling summary of the conversation so far, the item on stage and the next one with their notes, the clock state computed from the presenter's clocks) and the last `context_minutes` (4) of the transcript, and answers strict JSON: at most three hints of at most twelve words, typed `next_q`, `notice` or `recap`, or none. `claude_haiku` first, then `gemini_flash_lite`, then the local model, within one `hint_timeout_s` (20 s) budget. Every answer, with its model and latency, goes to `live/copilot/hints.jsonl` |
| Summary | every `summary_interval_s` (5 min), on the next line heard, the lines since the last refresh and the previous summary go to the same model chain (a 60 s budget: a summary is never urgent), which rewrites the summary of the whole conversation in at most 300 words. It is kept in `live/copilot/summary.json` (`{at, model, text, words}`), so Copilot switched off and on again keeps it; each refresh is logged in `hints.jsonl` as `reason: summary`. A refresh that fails keeps its lines for the next one |
| Drift | every 15 s and on every item change the copilot measures the clocks itself (the presenter's arithmetic): once the section on stage, or the whole session outside any section, runs `drift_hint_minutes` (5) behind the plan, it shows one `drift` hint ("12 min behind the plan in Ideas") with no model call, once per section. Models are never asked for time warnings, and a drift hint stays first when a model's hints arrive |
| Caps | at most `hint_calls_per_hour` (60) hint requests and `summary_calls_per_hour` (15) summary refreshes in any sliding hour, so a chatty call cannot run up model calls. A request is counted when it is queued for the hub (one count per request, whichever model in the chain answers it), and refunded if a newer one replaces it before it is sent. Past the cap nothing is asked until the oldest call in the window is an hour old |

Hints belong to the item they were asked for: an item change clears them at once and an answer that comes back for an item no longer on stage is dropped (logged as `stale`). They live `hint_ttl_s` (120 s). A stale hint is never shown.

## The presenter

The Copilot card shows the switches, what is being recorded, the hints (each with its type) and every failure on its own line; the header carries a **Copilot** chip. The failures are kept apart:

| Shown as | Meaning |
|---|---|
| Mic · not recording / Call audio · not recording | that device could not be opened (no such device, none at all); the other channel carries on |
| Transcript N s behind | chunks are waiting for the hub: the oldest ended N seconds ago |
| Hub down | nothing answers at `copilot.hub_url` |
| Transcription failed | the hub answered a chunk with an error or not in time |
| Hint timed out | no model answered within the budget |
| Hint failed | the models answered with errors, or not with the hints JSON |
| Summary failed | no model gave the summary; the lines wait for the next refresh |
| Hint cap reached / Summary cap reached | a warning, not a failure: the hourly cap holds calls back, and the line says when the next one is allowed |

None of them blocks the session: capture, transcription and hints run on their own threads, and the stage, the keys and the timers never wait for them.

After the session, the Results tab's **Conversation** card reads the transcript back with the hints under their items (see [results.md](results.md)).

## The overlay

A native window on top of every app, on whichever screen you pick (a prompter monitor in front of the camera, say). It is transparent, lets every click through and shows nothing until there is a hint; then it pops up, holds and fades. Two modes:

- **The hint itself** — the card with the hints.
- **Only a cue** — a small, gently pulsing icon meaning "look at the presenter": the words are on the presenter's Copilot card, so the overlay never covers the call.

**Settings → Live tools → Copilot overlay** sets it up: on or off, the screen (the monitors this PC shows, by name; one unplugged falls back to the primary), the position (a corner, an edge's centre or the centre, at a distance from the edge), the mode, and the look (shape, width, font, text size, colours, background opacity, how long a hint stays). Every change moves an open overlay at once and puts a sample there; **Show a sample** does the same without a change. Sizes are in each monitor's own pixels, scaled by its DPI.

The overlay is its own process (`python -m app.tray.overlay`), a thin client of the live socket (`/ws?role=overlay`): the server keeps every bit of state and the overlay only draws what the snapshot's `copilot` says. The server starts it with Copilot (or for a sample, stopping it a minute later if Copilot is still off) and stops it when Copilot goes off; it exits by itself when the server's process is gone. Its log is `data/logs/overlay.log`.

## Configuration

`copilot` in `config/config.json` (every key in `config/config.sample.json`):

| Key | Meaning |
|---|---|
| `hub_url` | the local LLM hub (empty: no hub is asked; the card says so) |
| `capture` | `devices` (the mic and the loopback, the default), `files` (`feed_dir`'s `me.wav` and `them.wav`, 16 kHz mono, played in at real-time pace: a dry run with no audio device) or `off` (only an injected transcript) |
| `mic_device`, `call_device` | a fragment of the device's name; empty = Windows' default input and output |
| `transcribe_model`, `hint_models`, `local_model` | the hub's model ids for the chunks, the hint chain and local only |
| `hint_timeout_s`, `hint_interval_s`, `hint_ttl_s`, `context_minutes` | the hint budget, how often a **them** line may ask, how long a hint lives, how much transcript the model reads |
| `summary_interval_s`, `drift_hint_minutes` | how often the rolling summary is refreshed; how far behind the plan a section runs before the drift hint (0: never) |
| `hint_calls_per_hour`, `summary_calls_per_hour` | the caps per sliding hour (0: no cap) |
| `silence_dbfs`, `min_chunk_s`, `max_chunk_s`, `silence_ms` | the chunker: the level under which a 20 ms frame is silence, and the cut rules |
| `overlay` | the overlay's settings, as Settings writes them |

`POST /api/copilot/transcript` (this PC only) adds lines as if heard and asks for hints at once: what the end-to-end test and a rehearsal without a call use.

## Dry run and latency

`scripts/copilot_dry_run.py` makes a synthetic two-voice conversation with Windows' own speech voices (no recording of anyone), boots a disposable instance with `capture: files` against the real hub, takes a test session live, switches Copilot on, moves through the items while the audio plays, and prints the chunk-to-transcript and transcript-to-hint latency from `transcript.jsonl` and `hints.jsonl`, with the model calls per kind (hints, summaries), the computed drift hints and the requests a cap held back. It never touches the live app on 8449.
