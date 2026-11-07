# Live copilot

During a live online session (a workshop, an interview, an important call), Copilot records your mic and the call's sound, transcribes them in short chunks through the local LLM hub, and has a model read the running transcript together with the session's plan and notes to suggest a few discreet hints: what to ask next, what to notice, that the agenda is slipping, a point worth summing up. The hints show on the presenter and on an overlay window only you see. The recording itself is never hidden.

**Consent.** Recording people needs their informed consent and a visible notice; in Spain and the EU, staying in the call is not consent. Tell the participants before you switch Copilot on. The presenter's Copilot card shows a **Recording** mark the whole time it runs. (Not legal advice.)

Copilot will not record until you have confirmed it for the session. The first time you switch it on, the presenter asks whether the participants were informed and agreed; **Confirm and switch on** records that as `copilot_consent: true` in the session's `session.yaml` checklist and as a `copilot_consent` event in `live/events.jsonl`, and the question is not asked again for that session. The server enforces it, so a Stream Deck button or the phone remote that flips `copilot_toggle` before the confirmation is refused with `consent_required` (the REST call gets a 409, the presenter and the remote show the message) and nothing records. The Sessions readiness checklist has a **Copilot consent** row that shows whether the confirmation is recorded. The app keeps your confirmation; it does not judge whether the consent was valid.

## Switching it on

The presenter's **Copilot** card, under the notes, has the switch (also the live action `copilot_toggle`, so a Stream Deck button or the phone remote can flip it). It works only while a session is live and is off whenever a session goes live, closes or is replaced: a restart never starts recording by itself.

The card's **Local only** switch belongs to the session (saved as `copilot_local_only` in its `session.yaml`): hints then come from the local model alone (`copilot.local_model`), for a meeting whose content must not reach a cloud model. Transcription always goes to the hub's whisper.

## Interview mode

The card's **Interview** switch is for a remote one-to-one interview. It belongs to the session too (saved as `copilot_mode: interview` in its `session.yaml`, `workshop` otherwise) and shows a field for the interviewee's name, saved as `copilot_interviewee` on Enter or when the field loses focus (Escape puts the saved name back). Both are the presenter's: a Plan-tab save keeps them as the file has them, like local only. The route is `PUT /api/copilot/mode` (`{mode, interviewee}`; an `interviewee` left out keeps the saved name): 409 when no session is live, 422 for a mode that is neither `workshop` nor `interview`.

In interview mode the hint and summary prompts change shape: `me` is the interviewer and `them` is the interviewee, named in the transcript the model reads (`interviewee` when no name is saved). The hints favour `next_q` follow-ups built on what the interviewee just said and `notice` hints about questions they have not answered yet; `recap` is rare. The summary tracks what the interviewee said on each topic and what is still unanswered. `transcript.jsonl` keeps the channel names `me` and `them` either way; the Results tab's **Conversation** card shows the interviewee's name instead of *Them*. Switching while Copilot is on applies to the next hint and asks for one at once (logged as `reason: mode`). The workshop prompts are unchanged, and the mode changes nothing about consent or the **Recording** mark.

## What happens while it is on

| Step | What |
|---|---|
| Capture | two channels at 16 kHz mono: **me** = your mic (Windows' default input, or `copilot.mic_device`), **them** = a WASAPI loopback of the call's output (the default output, or `copilot.call_device`): everyone else on the call. Both are written to `live/copilot/me-<time>.wav` and `them-<time>.wav` in the session folder as they arrive |
| Chunks | each channel is cut by its level: the first pause of at least `silence_ms` (600 ms) once a chunk is `min_chunk_s` (20 s) long, and at `max_chunk_s` (45 s) whatever is being said. A chunk with almost no voice (silence, a click) is dropped before it reaches whisper, which invents text on near-silence |
| Transcript | each chunk goes once to the hub (`POST /v1/audio/transcriptions`, `model=whisper` — not the default `transcribe` role, which drops Spanish code-switched tails — in the session's language) and is appended to `live/copilot/transcript.jsonl` as `{t0, t1, speaker, text, lag_ms}`. The transcript is never sent again as a whole |
| Hints | asked on a new **them** line at most once per `hint_interval_s` (45 s), and on every item change. The model reads a stable prefix (the session's title, the rolling summary of the conversation so far, the item on stage and the next one with their notes, the session's `playbook.md` cut to its first 1,500 characters like the notes, the clock state computed from the presenter's clocks) and the last `context_minutes` (4) of the transcript, and answers strict JSON: at most three hints of at most twelve words, typed `next_q`, `notice` or `recap`, or none. `claude_haiku` first, then `gemini_flash_lite`, then the local model, within one `hint_timeout_s` (20 s) budget. Every answer, with its model and latency, goes to `live/copilot/hints.jsonl` |
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

Under the switches the card carries a one-line **status strip**: a live level for each channel (your mic, the call) and how the latest hint check ended (*hint shown*, *checked: nothing worth a hint*, *dropped, the item changed*, or *waiting for the other side to speak* before the first). **Details** opens a popover laid exactly over the Next and Music cards (inline on a narrow or touch screen) so the cockpit never grows or scrolls:

- **Status** — per channel, when its last chunk was transcribed.
- **What it reads** — the item's notes (with an **Edit** link to the session) and the session's playbook (**Open** edits `playbook.md` in the default editor; without one, a line says so).
- **Where hints show** — the overlay's screen, position and mode (or that it is off), **Show a sample**, a link to Settings → Live tools → Copilot overlay, and **Test copilot**.
- **How the copilot works** — a short collapsible help.

**Test copilot** runs one hint check on what your own mic said, so a solo dry run shows a real hint end to end: it is refused when Copilot is off or when nothing was said lately (`POST /api/copilot/test`, 409 `copilot_off` / `nothing_heard`). The hints it produces carry a **Test** label on the card and on the overlay. While Copilot is on, the panel polls `GET /api/copilot/state` once a second for the levels.

None of them blocks the session: capture, transcription and hints run on their own threads, and the stage, the keys and the timers never wait for them.

After the session, the Results tab's **Conversation** card reads the transcript back with the hints under their items (see [results.md](results.md)).

## After the call: the recorder's tracks

A one-to-one call held on content-management's podcast recorder leaves each side's own camera and mic as a separate take, at full quality, in the episode folder's `video editing/`: `recorder - host - <date time>.mp4` for you and `recorder - guest - <date time>.mp4` for the other side, each with a `.json` sidecar. These takes exist only after the recorder is stopped, so they cannot feed the live copilot. The after-call pass (`src/copilot/after_call.py`, `src/copilot/recorder_tracks.py`) builds a better transcript from them:

- **Where.** On the Results tab's **Conversation** card, type the episode folder (or its `video editing/` folder) and press **Transcribe**. The folder is saved as `copilot_recorder_folder` in the session's `session.yaml`; a Plan-tab save keeps it. The pass refuses to run until the session's copilot consent is confirmed (the same `copilot_consent` the live copilot asks for), and only from this PC.
- **Which takes.** Only the sidecars whose `kind` is `main` count; the `remote-ref` files (the other side as heard over the call) are left alone. A take is used when it overlaps the session's live time, from its first `session_live` to its last event in `live/events.jsonl`, so a take from another day in the same folder is skipped. A side that stopped and started again has several takes, and each one is read.
- **Time.** A take's start is the time in its file name: when the recorder's server took its first chunk, in this PC's local time to the second, the same clock as the session's events. The guest's take also carries its upload delay, so the two sides line up to within about **1–2 s**. Lines are ordered by that time, so in a quick back-and-forth two short turns can swap places. content-management's sync stage measures the exact offset for the podcast, and this pass does not repeat it.
- **Transcription.** `ffmpeg` (on the PATH) reads each take's audio as 16 kHz mono. It is cut at pauses into chunks of one to three minutes, and each chunk goes once to the hub (`POST /v1/audio/transcriptions`, `copilot.transcribe_model`, the session's language). Each whisper segment becomes a line. Chunks with almost no voice, and whisper's own "nothing here" words, are dropped as they are live. No audio is written: the samples are read from the take as the chunks are cut.
- **The transcript.** `live/copilot/recorder-transcript.jsonl`, one record per line, oldest first: `t0`/`t1` (epoch ms), `speaker` (`me` for the host, `them` for the guest, the live channel names), `side` (`host` / `guest`), `name` (the interviewee's on a guest line in interview mode, empty otherwise), `track` (the take's file name) and `text`. Each run rewrites it whole. The live `transcript.jsonl` is never touched.
- **The summary.** Once the transcript is written, one summary of the whole call is asked of the hint models (the local model alone for a **Local only** session), with the session mode's summary prompt: the interview one in interview mode, the workshop one otherwise. It is saved as `live/copilot/recorder-summary.json` (`{at, model, text, words, lines}`), apart from the live rolling `summary.json`. **Summarise again** asks once more over the transcript already written.
- **Failures stay apart.** A run that cannot start says why at once: no consent, no hub configured, no folder typed, no folder there, no takes in it from while the session was live, or one already running. During the run, the hub down stops it and no transcript is written. A chunk that timed out or failed is counted as missed, and the run goes on without it. A run where every chunk failed writes nothing. Without `ffmpeg`, or with a take it cannot read, the run stops. The summary has its own states: the hub down, timed out, a failed answer, or nothing said. A failed summary is saved as `{at, failure, detail, lines}` and the transcript stands. When **Summarise again** fails, a summary already saved is kept.

The recorder's takes are a recording the participants agreed to for the recorder. The after-call pass changes nothing about consent or the **Recording** mark. The recorded transcript and summary are kept like the live transcript; `recording_retention_days` removes only the copilot's own `*.wav`.

## The overlay

A native window on top of every app, on whichever screen you pick (a prompter monitor in front of the camera, say). It is transparent, lets every click through and shows nothing until there is a hint; then it pops up, holds and fades. Two modes:

- **The hint itself** — the card with the hints.
- **Only a cue** — a small, gently pulsing icon meaning "look at the presenter": the words are on the presenter's Copilot card, so the overlay never covers the call.

**Settings → Live tools → Copilot overlay** sets it up: on or off, the screen (the monitors this PC shows, by name; one unplugged falls back to the primary), the position (a corner, an edge's centre or the centre, at a distance from the edge), the mode, and the look (shape, width, font, text size, colours, background opacity, how long a hint stays). Every change moves an open overlay at once and puts a sample there; **Show a sample** does the same without a change. Sizes are in each monitor's own pixels, scaled by its DPI.

The overlay is its own process (`python -m app.tray.overlay`), a thin client of the live socket (`/ws?role=overlay`): the server keeps every bit of state and the overlay only draws what the snapshot's `copilot` says. The server starts it with Copilot (or for a sample, stopping it a minute later if Copilot is still off) and stops it when Copilot goes off; it exits by itself when the server's process is gone. Its log is `data/logs/overlay.log`; a start that dies before that log exists (an import error) leaves its error in `data/logs/overlay-stderr.log` and a warning in the server log. While Copilot is on with the overlay enabled the server starts a dead overlay again within 15 seconds. **Show a sample** and a saved change toast "A sample is on the overlay" only when an overlay process is up to draw it, otherwise "The overlay is not running".

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
| `recording_retention_days` | how long `live/copilot/*.wav` are kept: 0 keeps them for good (the default), a number removes the ones last written longer ago than that when the session is opened (in the Sessions tab or on going live), each removal logged. Set in **Settings → Live tools → Copilot recordings**. The transcript, hints and summary are never removed |
| `silence_dbfs`, `min_chunk_s`, `max_chunk_s`, `silence_ms` | the chunker: the level under which a 20 ms frame is silence, and the cut rules |
| `overlay` | the overlay's settings, as Settings writes them |

`POST /api/copilot/transcript` (this PC only) adds lines as if heard and asks for hints at once: what the end-to-end test and a rehearsal without a call use.

## Dry run and latency

`scripts/copilot_dry_run.py` makes a synthetic two-voice conversation with Windows' own speech voices (no recording of anyone), boots a disposable instance with `capture: files` against the real hub, takes a test session live, switches Copilot on, moves through the items while the audio plays, and prints the chunk-to-transcript and transcript-to-hint latency from `transcript.jsonl` and `hints.jsonl`, with the model calls per kind (hints, summaries), the computed drift hints and the requests a cap held back. It never touches the live app on 8449.
