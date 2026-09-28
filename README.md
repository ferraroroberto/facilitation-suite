# facilitation-suite

One local app for running a live online workshop:

- a **stage** (a full-screen browser window on the display OBS already captures, so Zoom sees it through the OBS virtual camera),
- a **presenter** cockpit on the second monitor (on stage now, what comes next by title, notes, clocks, the live Zoom chat),
- **activities fed by the Zoom chat**: participants just type in the chat and the stage comes alive (word cloud, map, scale, cards, feed), read locally through Windows accessibility, with **no bot joining the meeting**,
- **breakout groups** (pairs, then two rounds of four with no repeats), per-item **timers**, and **results** after the session.

Slides stay authored in PowerPoint and are imported as images. Everything runs on one Windows PC; nothing leaves it except Zoom itself.

The design and the build plan are the epic issue (#1); each step is a sub-issue.

## Requirements

- Windows 10/11, Python 3.14
- PowerPoint desktop (slide import through COM)
- Zoom desktop with the meeting chat **popped out** (the chat reader targets that window)
- OBS Studio 28+ with obs-websocket enabled (optional: scene switching per item)

## Setup

```powershell
py -3.14 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m playwright install chromium
copy config\config.sample.json config\config.json   # then edit session_root, OBS password
```

Never activate the venv; invoke its interpreter directly.

## Run

| Command | What |
|---|---|
| `tray.bat` | tray icon that owns the server on **:8449** (idempotent; put it in the Startup folder) |
| `tray.bat --restart` | orphan-proof restart; verifies the served build (`/api/version`) matches `HEAD` |
| `webapp.bat` | foreground server (dev / headless) |

Open the app, `/presenter` on the second monitor, and `/stage` full-screen (F11) on the display OBS captures — at `http://127.0.0.1:8449/` until HTTPS is set up (below), then at `https://<this PC>.<tailnet>.ts.net:8449/` (the tray's **Open** uses it). This PC never needs a token either way.

The header's moon/sun button switches light and dark, and **Settings → Text size** (Small / Default / Large) scales the app's text; both are remembered per device (`facilitation-suite.theme` / `.textsize` in the browser's storage). The stage keeps its own look and sizes.

**HTTPS (for the phone remote):** `& .venv\Scripts\python.exe scripts\gen_tailscale_cert.py` writes a Tailscale certificate (a real Let's Encrypt leaf for this PC's tailnet name) to `webapp/certificates/`; after `tray.bat --restart` the server speaks HTTPS only, on the tailnet name. The leaf lasts ~90 days and renews itself at every start (`--check`), so there is no date to remember.

**Restart matrix:** anything under `app/` or `src/` → `tray.bat --restart`. Static files (`app/webapp/static/`) are served `no-cache`, so a browser reload picks them up without a restart.

**Logs** live in `data/logs/` (under `FS_DATA_DIR` when it is set). Each file has exactly one writer, because Windows cannot rotate a file another process holds open; each rotates at 1 MB and keeps 3 backups (`.log.1`–`.log.3`):

| File | Written by |
|---|---|
| `facilitation-suite.log` | the webapp (the server, including `✅ facilitation-suite up — build <sha>`), plus the lines of the freeze-PNG and session-PDF helpers it spawns — they log to stderr and the server relays them |
| `tray.log` | the tray: starting, adopting and restarting the webapp |
| `chat-reader.log` | the Zoom chat reader |
| `webapp/watchdog.log` (repo root) | the tray's watchdog breadcrumbs |

## Importing a PowerPoint

Sessions → **Import PowerPoint** (type the path or **Browse**, which opens the Windows file dialog on this PC). PowerPoint desktop exports every slide to a 1920×1080 PNG through COM, from a read-only copy of the deck, in its own process with a 5-minute timeout. Each slide keeps PowerPoint's own SlideID, title, notes and fingerprints (image dHash + title/notes hashes) in `slides/slides.json`.

- **Slide text in the stage lettering.** A slide's plain text shapes (not grouped, not rotated, not in a table) are also recorded — position, size, colour, alignment — and the slide is exported a second time without them (`slide-<id>-bg.png`). The stage draws that picture and the text on top in the session's lettering — its title in the title lettering, the rest as slide text (see *Stage lettering*) — shrinking a box if the font runs wider than PowerPoint's. Per slide, the Plan tab can keep **As in PowerPoint** (the original picture) or set the title's and the text's own font and capitals. Decks imported before this need one re-import (nothing to review; **Apply** adds the text layer).
- **OBS profile per slide** comes from where the slide reserves the camera: a flat grey box (or empty area) on the right → *Camera strip*; a small box top-right → *Camera PiP*; nothing reserved → *Screen only*. A notes line `[obs: pip|strip|screen]` overrides it; an unsure slide stays unset and is flagged.
- **First import builds the plan:** PowerPoint sections when the deck has them, otherwise a new section at every title-only divider slide. Placeholder slides become plan items: a slide titled `activity – <question>` (or the older `streamalive N – <question>`) becomes an activity (map / scale / word cloud guessed from the words), `breakout – <title>` a break, `mentimeter …` a skipped slide.
- **Re-import changes nothing until you review it.** The export waits in `slides/incoming/` and the Plan tab opens the review: slides are matched by SlideID first, then — for a slide PowerPoint re-created with a new id — by image and text fingerprints, and each one is *identical*, *modified* (image, title or notes — it says which, "Notes changed · 2 lines added"), *new*, *removed* or *moved* (the fewest moves that explain the new order), with the old and new picture side by side. Every change has its own switch; **Apply** does only those. An activity stays anchored to the slide before it: a moved slide takes its activities along, and when a slide is removed its activities follow the previous surviving slide (the review says which). A declined change keeps the old picture/title (modified), keeps the slide (removed), keeps the plan order (moved) or leaves the slide out (new — the next re-import offers it again). **Back** leaves the review waiting (a banner in the Plan tab); **Cancel** throws the export away.

`FS_TEST_POWERPOINT=1` runs the one test that drives the real PowerPoint (on a synthetic deck it builds itself).

## Planning

The **Plan** tab edits `session.yaml`: sections with planned minutes (drag to reorder, rename, collapse — or **Collapse all / Expand all**; every load opens them all), and inside each section the slides, activities and breaks in order (drag, or Alt+↑/↓, or Move up/down). **Add slide or activity** at the end of a section also adds a **section after this one**. Each item has:

- an **OBS profile** (Camera strip / Camera PiP / Screen only; slides start with the detected one),
- its **own timer** — no global defaults, decided item by item: duration, when it starts (manually, when the item opens, with the capture), where it shows (stage / presenter / both) and what happens at 00:00 (keep showing 00:00, remove the timer from the stage, stop the capture, next item, chime); a paused timer shows yellow on the stage, the presenter and the phone; on a slide it sits in a bottom corner over the slide (the left one when the camera takes the right),
- its **music** (optional — see *Music*): a track from the session's `audio/` folder that plays with the item's timer or when the item opens,
- **In this session** (off = skipped live, kept in the plan),
- for a **breakout** (Add slide or activity → Breakout): its title, the round in the rooms (Pairs, Groups of 4 · A or B — the stage says it with the room count from the Groups tab) and its clock, ten minutes by default,
- for activities: type, title (the name in the plan and on the presenter; the question when empty), question, question font (the session's stage font, or one installed on this PC) and size (in stage pixels on the 1920×1080 canvas), the prompt to paste in the chat, and the type's own answer options,
- **Notes** for the presenter's Notes card — a slide starts from its PowerPoint notes, and editing them here leaves the deck as it is (clear the box to get the deck's back).

Type `\n` in a question or title where the line should break on the stage; lists, the presenter and the results show it on one line. **Duplicate** (or Ctrl+D) copies the selected items right after the last of them. The "who are you with?" preview draws the rooms from the Groups tab (it redraws after a shuffle), and the stage shows the round and room count under its title.

Select several items as in a file manager — **Ctrl+click** adds or removes one, **Shift+click** selects a range (Ctrl+Shift+click adds it), **Shift+↑/↓** extends, **Ctrl+A** selects all, **Esc** keeps one — and the editor becomes a bulk panel: one OBS profile or **In this session** for all of them, **Duplicate** or **Delete**. Dragging any selected item moves the whole selection; **Delete** on the keyboard deletes it. Deleting an imported slide only takes it out of the plan: it stays in the deck, and **Add slide or activity → Slide from the deck** brings it back.

**Stage lettering** (Sessions tab) — two fonts. The **title font** letters titles and questions: Patrick Hand, a font installed on this PC, or **Font file…** (an `.otf`, `.ttf` or `.woff` on this PC, your own handwriting font, say), its **weight** (regular / bold) and **line thickness** in stage pixels (a stroke under the letters, so they keep their shape). The **text font** letters everything else — by default the chat hint's plain sans, or an installed font, regular or bold. Then **each kind of text** takes either font, in **ALL CAPS** or **as typed**: titles and questions (the title font, in capitals, by default), subtitles, the chat hint ("Write your answer in the chat"), the answers (word cloud, cards, feed, scale, map, groups) and a slide's text other than its title (all four in the text font, as typed, by default). A slide's title is the box PowerPoint marks as its title, else its biggest text. It all goes into `session.yaml` (only what differs from the defaults):

```yaml
font:
  file: C:/Users/you/Fonts/MyHand-Regular.otf   # else family: Georgia
  weight: 400
  stroke_px: 1.5
  caps: true              # titles and questions in capitals
  text_family: Georgia    # empty = the chat hint's plain sans
  text_weight: 400
  roles:                  # title, sub, hint, answers, slide_text
    answers: {caps: true}           # the word cloud in capitals
    hint: {font: title, caps: true} # the chat hint in the title font
```

Every item follows it; in the Plan tab an item's title **font** row (font, size, capitals — "as the session" by default) and its **Other text** rows (the chat hint and answers of an activity, a breakout's subtitle, a slide's text: font — the title font, the text font or an installed one — and capitals) are the exceptions for that item only (`font.roles` on the item).

The file stays where it is (keep it on this PC — the readiness list warns if it goes missing, and the stage then falls back to Patrick Hand); it is served to the stage, the presenter's previews, the phone remote and the frozen captures from `/api/sessions/<id>/font`.

Activity types are plug-ins: one folder per type under `app/activities/<type>/` with an `editor.json` (label, icon, options). Edits are staged; **Save to session.yaml** is the only write.

## Presenting live

**Open presenter** (Sessions tab) makes the session live and opens `/presenter` on the second monitor; **Open stage window** opens `/stage` — drag it to the display OBS captures and double-click it for full screen. Both follow one state owned by the server and pushed over the `/ws` WebSocket: every view only sends *intents* (next, blackout, timer…), so the stage and the presenter can never disagree.

| Key (stage or presenter window) | Action |
|---|---|
| → · PageDown · ↓ · N · click | next item (a presentation clicker works; so does a click on the stage window or the presenter's "on stage now" — a double-click on the stage is full screen) |
| ← · PageUp · ↑ · P | previous item |
| B · . | blackout |
| T | the item's timer: start / pause |
| M · + | the item's timer: +1 min |
| Space | capture start / stop (activities); on any other item with a timer, timer start / pause |
| Home · End | the first · the last item |

The presenter shows what is on stage, the next item and the three after it **by title**, the speaker notes (and, for an activity, the prompt to paste in the chat with a Copy button), the item's timer controls, and the presenter-only clocks: the session clock against the planned duration (ahead / behind), the time left in the current section and when the next break is due. Click any thumbnail in the filmstrip to jump there. **Start the session over** (the ↺ next to ×) goes back to the first item with no clocks, timers, captures or chat — after a rehearsal, say; nothing is deleted: the run so far stays in the session folder as `live-<date>-<time>/`.

The live position, clocks and timers are mirrored to `live/state.json` (a restarted server resumes where it was) and every item change, clock and timer event is appended to `live/events.jsonl`. Saving the plan while live reloads it in place. The stage's look comes from `themes/default.css` plus the session's own optional `theme.css`; **Session details** sets the session's **language on the stage** (English or Español: the words the stage says by itself — the hint under activities, "Write your answer in the chat" / "Escribe tu respuesta en el chat", default titles such as "Break" / "Descanso", the breakout rounds and room counts) and, optionally, a hint of your own instead of the language's.

## Music

An item can carry **music**: a file, or a Spotify playlist, album, artist or track — played on this PC's own audio output (the app plays it itself, so it never depends on a browser tab; route the PC's audio into Zoom/OBS as you already do). In the Plan tab, the item's **Music** row: switch it on, pick **File** or **Spotify** — **Choose file…** (mp3, wav, ogg or flac — m4a/AAC is not decoded; convert it first) or paste the Spotify link (Share → Copy link) — then its volume, fade-in and fade-out seconds, **Starts** and **When leaving the item**, and **loop** (start over at the end). The file is **copied into the session folder** as `audio/<name>` (the way the roster is copied), so the session stays self-contained and *Files offline* covers it; a different file with the same name becomes `<name>-2`. In `session.yaml`:

```yaml
- kind: slide
  slide_id: 105
  timer: {seconds: 300, start: manual}
  music:
    path: audio/warm-up.mp3   # session-relative
    volume: 70                # 0–100
    fade_in_s: 3
    fade_out_s: 3
    loop: true
    start: with_timer         # with_timer | on_enter | manual
    on_leave: fade_out        # fade_out | keep_playing
- kind: break
  music: {source: spotify, uri: "https://open.spotify.com/playlist/…", start: on_enter}
```

**With the timer:** starting or resuming the item's timer fades the music in and plays it; pausing the timer fades it out and pauses it; **Reset**, or the timer reaching 00:00, fades it out and stops it. `on_enter` plays when the item comes on stage (its timer still pauses and stops it); `manual` plays only from the presenter. Leaving the item fades it out and stops it, unless it is set to **keep playing** (then its own timer, still running, stops it at 00:00). A file that ends by itself just stops, or starts over with loop on.

**By hand:** the presenter's **Music** card (shown when the session has music, or Spotify is set up) plays any of the session's tracks — every item's, plus any other file in `audio/` — or a pasted Spotify link, with the item's own music picked when it comes on stage: **Play / Pause / Resume**, **Stop** (fades out over the track's fade-out), **Fade out** (a slow 8-second wind-down, then stop) and a volume slider. The phone remote has the same play/pause, stop and volume. The Stream Deck actions are `music_toggle` (with nothing playing: the item's music, else the last track), `music_stop`, `music_fade_out` and `music_volume/<0–100>`.

**Which wins:** the latest command. An item's timer and leaving the item only act on music **that item started itself**: music you play by hand is never paused or stopped by a timer, and a timer start on an item with *with the timer* music replaces whatever plays (the same track already playing is taken over, not restarted). Pausing, resuming or changing the volume by hand keeps the item in charge; stopping ends it.

The presenter's chip says *Music · <track>* (playing), *paused*, *idle* or *error* — with the reason, e.g. no audio output device (speakers or headset disconnected) or a file missing from `audio/`; a music problem never stops the deck. The readiness list's **Music files** check (only when the session plays files) says whether every track is in the folder, on this PC and playable; its **Spotify** check (only when the session plays Spotify) says whether the login works and the desktop app on this PC is visible. Nothing of the music is saved: after a restart the app starts silent. Music starts and stops are logged (`facilitation-suite.log`, *sound is coming out* once the device plays) and appended to `live/events.jsonl`.

### Spotify setup (once)

Spotify is driven through its official Web API, on the **Spotify desktop app of this PC** (a **Premium** account — Spotify allows playback control only for Premium). The app keeps its client id and login in `.env` (gitignored) — never in the repo or `config/config.json`.

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard), log in with the Premium account and **Create app**: any name and description, **Redirect URI** `http://127.0.0.1:8765/callback` (exactly — Spotify only accepts the loopback address, not `localhost`), API **Web API**. Save.
2. In the app's **Settings**, copy the **Client ID** into `.env` at the repo root: `SPOTIFY_CLIENT_ID=<client id>`. (No client secret: the login uses PKCE.)
3. Open the Spotify desktop app on this PC, logged in to the same account.
4. Run `& .\.venv\Scripts\python.exe scripts\spotify_login.py`: the browser asks you to allow the app; the script writes `SPOTIFY_REFRESH_TOKEN` to `.env` and prints the account type and the devices Spotify sees (it ends with *The desktop app on this PC is ready*). No restart needed. Another port: `--port 8766` (and that redirect URI in the dashboard).
5. The app plays on the `Computer` device named like this PC; if Spotify shows it under another name, set `SPOTIFY_DEVICE_NAME=<name>` in `.env`.

Fades step Spotify's volume twice a second; a stop fades out, pauses (Spotify has no stop) and puts the app's volume back where it was. The chip and the readiness list keep the failures apart: **not set up** (no client id or login in `.env`, or a wrong client id), **login expired** (revoked or expired — run the login again), **not open on this PC** (open the desktop app), **Premium needed**, and **unknown** when Spotify could not be reached or is rate-limiting — never counted as ready.

## Activities and captures

On an activity, **Space** (or the presenter's big button) starts the **capture**: every participant message that arrives from then on belongs to the activity, and the stage grows its visual live — a word cloud, scale bars with the average, cards, or a feed of bubbles. Space again stops it; a stopped capture can be reopened (what arrived while it was stopped stays out). Only one capture runs at a time. Your own messages ("You" in Zoom) never count — unless you turn on **Count my own messages (rehearsal)** under *Show names on stage* in the presenter's capture panel, for rehearsing alone by typing the answers yourself. While it's on, the presenter header shows a *Counting your messages* warning chip; it's never saved, so it is off again after a restart and whenever a session is made live. **Click a message** in the presenter's chat to hide it from the activity (a "can you hear me?"); click again to count it back.

At every stop the result is **frozen** in the session folder: `live/captures/<item>.json` (every answer with its name and parsed value, and the result) and `live/captures/<item>.png` (the stage as it looked, rendered by a headless browser). An item timer set to start *with the capture* starts with it; one set to *stop the capture* at 00:00 does. And on an activity a running timer always captures: starting or resuming the timer, or **+1 min** after 00:00, (re)opens the capture, and **Reset** on the timer stops it.

| Type | What counts | On the stage |
|---|---|---|
| Word cloud | **How answers become words** (Plan tab): *Automatic* (the default) keeps answers of up to three words as one phrase and splits longer ones into words minus filler words (Spanish/English lists); *Verbatim* keeps each answer whole as typed (spaces tidied, punctuation at its ends dropped, so "Saying yes!" and "saying yes" are one entry); *Words* splits every answer into words minus filler words, the classic cloud. Laughter dropped (in *Verbatim* only an answer that is nothing but laughter); case, accents and simple plurals merged (*Verbatim* shows the most common spelling as typed) | the cloud grows, most frequent biggest; a long verbatim answer wraps onto lines of about four words |
| Scale | the first number in range, or a keyword from the list (lowest → highest); one vote per person | bars with counts, the leader highlighted, the average |
| Map | a place, geocoded offline: `Milan, italy`, `Sevilla, España`, `CDMX`, `desde Bogotá`, a country alone (→ its capital); an ambiguous city follows the room ("Valencia" goes to Spain when the others are there); one pin per person, their latest answer | people pop in with their names, the view fits everyone, nearby pins merge into one with the count in its dot, each label takes a free side of its dot (or waits in the side list), a crowded region gets its own inset that stays put, click a pin for who is there; the headcount, countries and top cities alongside |
| Cards | each answer with its name | the latest cards in a grid |
| Feed | each answer with its name | bubbles, the newest at the bottom and largest |

Each type is a plug-in folder under `app/activities/<type>/`: `editor.json` (the Plan tab's fields and sample answers), `parse.py` (`parse(message, options)` → contribution, `aggregate(contributions, options)` → result; pure and unit-tested; optionally `report(result)` — the Results tab's summary line, top list and PDF tile — and `value(parsed)` — one answer in the Excel report) and `stage.js` + `stage.css` (draws a result). The Plan tab's stage preview is drawn by the same renderer with the sample answers, and the presenter's *Simulate answers* on an activity uses them too.

**Map answers that land nowhere** are listed on the presenter under *Not on the map* with the closest places as one-click buttons, or type the place and press Enter. Common chat spellings live in `app/activities/map/aliases.yaml` (add a line when an answer keeps landing there). The map's data is built once by `scripts/build_geo.py` (needs the network and `babel`) and committed; the app never downloads anything.

## Quiz

A Kahoot-style quiz is planned as items (#34; players play on their phones — see *Quiz player* — while the stage's lobby, leaderboard and podium and the presenter's quiz controls arrive in later steps). Three activity types, all in the Plan tab's activity **Type → More…** menu:

- **Quiz lobby** starts a game; its **Quiz name** is the title on the stage. The game is the quiz questions after it, in plan order, up to the next quiz podium.
- **Quiz question**: the question, **Answer 1–4** (two to four), **Correct answer(s)** (the answer numbers, as Kahoot writes them: `2`, or `1,3` for several), **Time limit** (5, 10, 20, 30, 60, 90, 120 or 240 s; 20 by default) and **Points** (standard, double or none).
- **Quiz podium** ends the game.

**Import Kahoot** (Plan tab toolbar) reads Kahoot's own spreadsheet import template (`KahootQuizTemplate.xlsx`, header row with *Question*, *Answer 1–4*, *Time limit (sec)*, *Correct answer(s)*; the questions end at the first empty question cell): pick the `.xlsx` on this PC, name the quiz (the file's name by default), and a new section at the end of the plan gets the lobby, one question per row and the podium — saved to `session.yaml` at once, with a planned-minutes estimate (every time limit plus 30 s each). A row that breaks the format (no correct answer, a correct answer pointing at an empty answer, fewer than two answers, a time limit Kahoot does not offer) refuses the whole file with a message naming the row, and nothing is saved. Kahoot's report `.xlsx` is not read. In `session.yaml`:

```yaml
- kind: activity
  type: quiz
  question: Which planet is known as the red planet?
  options: {answer_1: Venus, answer_2: Mars, answer_3: Jupiter, answer_4: Saturn,
            correct: "2", time_limit: 20, points: standard}
```

**The game** (`src/quiz/`) runs on the server; phones and the stage only render it. The first time the stage reaches a quiz lobby (or one of its questions) a game opens. Coming back to the lobby later resumes the same game, and `quiz_new_game` on the lobby starts a fresh one to play it again.

- **Phases.** Entering a question opens it with a deadline of its time limit. **Next** then steps *question → reveal* (the answers lock; the answer distribution and the correct answer show) *→ leaderboard*, and only from the leaderboard moves on to the next item.
- **Time up** reveals by itself, and so does `quiz_lock` (lock answers now).
- **Previous** always goes to the previous item. A question left while it is still open is locked. Coming back to it shows its reveal again, never a second chance to answer.
- **Other items.** On every other item, including the lobby and the podium, next and previous work as before.
- **Answers.** Answers are not captured from the chat. A quiz question never opens a capture window, and the Space key does not start one.
- **Scoring** follows Kahoot's published rule: a correct answer scores `round(1000 × (1 − (response time / time limit) / 2))`, full points inside the first half second, ×2 for **double**, 0 for **none**. A wrong or missing answer scores 0.
- **Response time** runs from when the buttons appeared on that phone, clamped to how long the server has been asking.
- **Streaks.** Answer streaks are counted and shown but score nothing, as in Kahoot today.
- **Ties** go to the smaller total response time over correct answers, then to who joined first.

Every join, answer, kick and phase change is appended to `live/quiz.jsonl`, and phase changes also go to `live/events.jsonl`. A restarted server replays `quiz.jsonl` and resumes every game with the same players, answers and scores. Scores are always derived, never stored. A question whose time ran out while the server was down is revealed at once. Stream Deck: `quiz_lock`.

## Quiz player (public)

The quiz (#34) is the one part of the app strangers reach: players on their own phones, on mobile data, not on the tailnet. So it is a **separate, minimal app** — never `:8449`, never `RemoteAuth` loosened:

- The server starts a second listener, the player app (`app/player/`), on **`127.0.0.1:8451`** (`quiz.public_port`), loopback only whatever `host` says. It runs in the same process and event loop as the main app and stops with it.
- It serves the player page `/play` (its own CSS and script under `/play/static/`), the player API `/play/api/…` and the `/play/ws` socket — nothing else: the presenter, the stage, the app, `/api/*`, `/ws`, `/static` and the OpenAPI docs are all `404` there. Only the player app may ever use port 8451. It calls the main app's one quiz engine directly (same process, same loop).
- **Tailscale Funnel** publishes it on the public internet as `https://<this PC>.<tailnet>.ts.net:10000/play`. Funnel only serves on 443, 8443 and 10000, and on this PC the other two are taken: 443 by the tailnet-only LLM hub, 8443 by voice-transcriber (a Funnel there would capture its tailnet traffic). Likewise 8450 is parking-manager's, hence 8451. The `tailscale serve` entries on 443, 3000 and 8465 stay tailnet-only and untouched.
- If 8451 is busy at start, the log says `❌ quiz player listener: 127.0.0.1:8451 is busy …` once, the public quiz is off, and the main app keeps serving. `quiz.public_port: 0` turns the listener off.

```powershell
tailscale funnel --bg --https=10000 http://127.0.0.1:8451  # publish (persists across reboots)
tailscale funnel status                                     # what is public (:10000 = "Funnel on")
tailscale funnel --https=10000 off                          # stop publishing
```

Never `tailscale funnel reset` — it clears the whole serve config, the tailnet-only entries included.

Set `quiz.public_url` to the public address (`https://<this PC>.<tailnet>.ts.net:10000`): the join QR code and link are built from it. While it is empty the presenter's chip says *Quiz · PIN … · public URL not configured* and the QR is a placeholder saying the same.

**Playing on a phone.** Every game has a **6-digit PIN** (kept in `live/quiz.jsonl`, so it survives a restart; *play again* gets a new one). Players scan the QR code (`quiz.public_url` + `/play?pin=…`, the PIN filled in) or open `/play` and type the PIN, pick a nickname (a name already taken gets ` (2)`) and join. The phone then shows only what it needs: *You're in* in the lobby; on a question, two to four big tiles in four colours **and** four shapes (1 red triangle, 2 blue diamond, 3 amber circle, 4 green square — the stage uses the same) with the time left; after a tap *Sending…* until the server acknowledges the answer, then **✓ Locked in** — never before; *Too late* when the question closed first; at the reveal *Correct* (+points, rank) or *Not this time* or *No answer*; the rank and score on the leaderboard and podium; and *Removed* for a player the host kicked. The page works from 320 px wide, in light or dark (the ☾/☀ button), and never receives the correct answer before the reveal or anyone else's answers.

- **Reconnect-safe.** The player's id and secret stay in the phone's browser storage: a reload, a locked phone or a Wi-Fi ↔ 4G switch resumes the same player and score. A PIN for another game starts a fresh join.
- **Acked answers.** A tap is retried with backoff until the server answers; a retry never scores twice (the first answer stands).
- **Live updates** come over the `/play/ws` socket; while it is down, or after it dropped twice within 30 s, the page polls every second and keeps trying the socket. `?transport=poll` forces polling (to test a network that blocks WebSockets).
- **Rate limits per phone address** on join/resume (200 at once, then 5 a second), answers (600, then 20 a second) and wrong PINs (30, then one every 2 s) — generous enough for 60 players behind one office NAT. Funnel passes the phone's public IP in `X-Forwarded-For` (replacing anything the phone sent), which the listener trusts from `127.0.0.1` only.
- The listener's own request lines stay out of the access log (the polling URL carries the player's secret, and 60 phones poll every second).

`GET /api/quiz/qr.svg` (main app, behind `RemoteAuth`; `?pin=` for a given game) is the join QR of the game on stage; its `X-Quiz-QR` header says `ok` or `not-configured`. `state.quiz` carries `pin`, `join_url` (`null` while `quiz.public_url` is empty) and `listener` (the player listener is up).

## Phone remote

`/remote` on the phone: what is on stage (a live preview), what comes next, the session clock and how far off the plan it is, and big buttons for **Next / Previous**, **Start / Stop capture** (on an activity), the item **timer** (start/pause, +1 min) and **Blackout** — the same intents as the keyboard — plus the **music** (play/pause, stop, volume) when the session has any. **Chat** shows the Zoom chat (tap a message to hide it from the activity, tap again to count it back); **Groups** shows the breakout rooms of each round to read out.

1. Set up HTTPS once (see *Run*).
2. **Settings → Phone remote → Make the phone link**, **Copy the link**, send it to yourself and open it on the phone (on the tailnet). Opening it pairs that phone: the server sets a 30-day cookie and the token leaves the address bar.
3. **New link** unpairs every phone that had the old one; **Turn off** locks every other device out.

Every other device needs that token for everything (the app, the API, the live connection) — as a pairing cookie, an `Authorization: Bearer` header or `?token=`; without it, `401`. The pages and API stay open to this PC itself: loopback, or this PC reaching itself through its tailnet name (the connection comes from the same address it arrives on). The token lives in `config/config.json` → `remote.token` (gitignored), is never logged (request lines are redacted), and the native file picker, the chat reader's endpoints and the remote's own Settings stay PC-only even with it.

## Results and exports

The **Results** tab reads the session folder, so it works on any session once it has run (live or not): every captured activity in the order it happened, and for the selected one the visual exactly as the stage showed it at the stop, its top items (words, votes, countries) and every answer with the person's name and chat time — hidden ones struck through.

Each **quiz game** is a row too, labelled by its quiz name and run (a replay with `quiz_new_game` is run 2): its podium, the final leaderboard (rank, nickname, score, correct answers, average response time, source *phone* or *chat*) and, per question asked, how many picked each answer with the correct one(s) marked. Everything is derived by replaying `live/quiz.jsonl` through the game engine (`src/quiz/results.py`), so the numbers are the ones the stage showed. Kicked players are left out; a lobby nobody joined is not a game.

- **Export session PDF** — `exports/session.pdf`: every slide at its first showing and each live result at its (last) stop, in the order of `live/events.jsonl`, one widescreen page each, then an appendix with every counted answer. Printed by headless Chromium in its own process. A capture whose image never rendered still gets a page saying so. A quiz adds a page per question asked (when it was asked: its distribution, or its frozen image if one exists) and a podium page per game (when the podium was shown, else right after its last question), and each game's leaderboard in the appendix.
- **Excel report** — `exports/report.xlsx`: a summary, one sheet per activity (name, time, answer, parsed value, hidden), two sheets per quiz game — `Quiz – <name>` (leaderboard, then each question's answers and how many picked them) and `Quiz – <name> answers` (every player × question: answer, correct, points, response ms), with ` run <n>` for a replay — and *Participation* (per person: answers per activity, total, chat messages — most active first). A session without a quiz gets exactly the same report as before.
- **Check against Zoom's saved chat** — pick the `meeting_saved_chat.txt` Zoom saved when the meeting ended (the picker opens in `Documents\Zoom`). The banner says how many of Zoom's messages the app has ("412 of 412 matched") and lists any missing, plus any only in the app. Reactions and simulated messages are not counted; emoji the reader cannot see and your own "You" messages still match. The report is kept in `exports/zoom-reconciliation.json`.

## Breakout groups

The **Groups** tab makes the 1-2-4-all breakout rooms (the algorithm ported from `facilitation-shuffle`, unchanged):

1. **Import roster (.xlsx)** — the first sheet, columns by header in any order: `name` (required; without a `name` header the first column is used), `role`, `company`, `country`, `present`, `email` (optional). The file is copied into the session folder as `roster.xlsx`; the original is never edited.
2. **Mark who is here** with the switches (they win over the file's `present` column and are saved in `groups.yaml`).
3. **Shuffle** — three rounds: **pairs** (an odd number gives one trio), **groups of 4 · A** (whole pairs merged, never split; 4k+2 people give one room of six), **groups of 4 · B** (re-mixed into rooms of four — a room of three or five takes the remainder, never fewer than three — keeping as few people as possible with someone from their round-A room — up to 120 000 tries). The note under the tabs says how well round B mixed.
4. **Copy rooms for Zoom** (`Room 1: Ana, Sam` lines to paste while assigning rooms by hand) or **Export Zoom pre-assign CSV** (`Pre-assign Room Name,Email Address`, also kept in `exports/`) — only when everyone present has an email; otherwise the tab says who is missing one.
5. **Add reveal slide** puts a "Who are you with?" item into the Plan tab's unsaved edits, right after the selected item (then Save there): the stage shows every room of that round, numbered.

If presence changes after a shuffle, the tab and the readiness checklist say so until you shuffle again.

## OBS

Each item in the plan has an **OBS profile** — *Camera strip*, *Camera PiP* or *Screen only*. When an item goes on stage the app switches OBS to that profile's scene over **obs-websocket v5** (built into OBS 28+; OBS → Tools → WebSocket Server Settings, default port 4455), and the stage keeps the profile's camera zone empty. **Settings → OBS profiles** picks each profile's scene from the scenes OBS has (or type one while OBS is closed), moves the camera zones, and holds the connection (host, port, password — the password is written to `config/config.json` and never shown again). The `obs_profile/<name>` action switches by hand (Stream Deck).

OBS is never in the critical path: it runs on its own thread, reconnects by itself, and the presenter's **OBS** chip says *profile …*, *not reachable* (click to retry), *connecting* or *off*. A profile without a scene, or a scene OBS doesn't have, leaves OBS as it is and the chip says so. The deck keeps working with OBS closed.

## Stream Deck

Every live control is one URL: `POST /api/actions/{action_id}` (or `/{action_id}/{arg}`), the same contract as home-automation's action alias, backed by the one intent list the keyboard and the presenter use (`src/live/actions.py`). **Settings → Stream Deck buttons** lists each button's URL with a Copy button: `next`, `prev`, `capture_toggle`, `timer_toggle`, `timer_add_minute`, `timer_reset`, `blackout`, `names_toggle`, `goto_section/<n>`, `obs_profile/<name>`, `music_toggle`, `music_stop`, `music_fade_out`, `music_volume/<n>`, `quiz_lock`. Calls from this PC need no token; any other device needs the phone-remote token (see *Phone remote*). A caller can name itself in `X-Automation-Source`; the presenter shows the last press as a chip. Once the app serves HTTPS, point the plugin at the tailnet name (`FACILITATION_SUITE_BASE_URL=https://<this PC>.<tailnet>.ts.net:8449` in its `.env`) — still no token, since it runs on this PC.

The physical keys come from the fleet Stream Deck plugin (`fleet-config/stream-deck`, its `Call Action` key with `"app": "facilitation-suite"` — fleet-config#1006).

## The Zoom chat reader

Participants answer in the Zoom chat; a separate local process reads it — no bot, no Zoom app. In Zoom, **pop out the meeting chat** (Chat → … → Pop out): the reader finds that window (`Meeting chat`) and reads every message through Windows accessibility (MSAA) twice a second, then posts the new ones to the server, which appends them to the session's `live/chat.jsonl` and shows them on the presenter.

- It starts when a session goes live (`reader.enabled` in the config), from the presenter's **Zoom chat** chip or card, or from the readiness **Test** in the Sessions tab. It stops with the live session and exits by itself if the server goes away.
- The presenter chip says what it is doing: *reading*, *pop out the chat* (window not found), *no answer* (no heartbeat for 3 s), *error*, *simulating* or *off*.
- Messages already in the window when it starts are history (Zoom keeps chat across meetings in the same room), so a restarted reader never duplicates what was stored. Emoji arrive as nothing, so prompts should ask for words or numbers. Private messages are not read.
- While it runs, the Windows "screen reader present" flag is on (Zoom may need it to expose the chat); the reader restores it when it stops.
- **Rehearse alone:** *Simulate answers* on the presenter replays fake answers through the same pipeline; from a terminal, `python -m src.chat.reader --server http://127.0.0.1:8449 --simulate burst:50` (or `random:30`, or a YAML script `{messages: [{after, sender, text}]}`).

Log: `data/logs/chat-reader.log` (see **Logs** under Run).

## Configuration

`config/config.json` (gitignored; `config/config.sample.json` documents every key):

| Key | Meaning |
|---|---|
| `host` | bind address for the tray's and `webapp.bat`'s server: `0.0.0.0` (default) listens on every interface, which the phone remote and the Stream Deck over the tailnet need; `127.0.0.1` keeps the app on this PC only (off the LAN and the tailnet — the tray's **Open** then uses the loopback URL). Restart the tray after changing it |
| `port` | server port (8449) |
| `session_root` | default parent folder for new sessions (`<root>\<workshop>\<session>\`) |
| `stage_display` | which display the stage window goes on (informational) |
| `obs` | obs-websocket host / port / password (the password stays in this file only), `enabled` = scene switching on/off |
| `profiles` | the three OBS profiles: each one's OBS `scene` and the camera `zone` the stage keeps empty (`[left, top, right, bottom]` as fractions, `null` = no camera) |
| `reader` | Zoom chat reader: poll interval and the chat window's class and title |
| `remote` | `token`: the phone remote's bearer token (a secret — made and replaced from Settings; empty = only this PC gets in) |
| `quiz` | `public_port`: the quiz player listener on `127.0.0.1` (8451; `0` = off) · `public_url`: its public Funnel address, empty until published (see *Quiz player*). Restart the tray after changing it |

**`.env`** (gitignored, repo root) holds secrets only: `SPOTIFY_CLIENT_ID`, `SPOTIFY_REFRESH_TOKEN` and the optional `SPOTIFY_DEVICE_NAME` (see *Spotify setup*); `FS_ENV_PATH` points elsewhere.

The **ledger** `sessions.local.yaml` (gitignored; example in `sessions.example.yaml`) lists session names and folders only. Each session lives in its own folder with its own `session.yaml`.

## Sessions

A session is a folder, by default `<session_root>\<workshop>\<session>\`:

```
session.yaml      the plan (schema v1) — human-readable, safe to edit by hand
slides/           slide PNGs + slides.json (titles, notes, fingerprints, detected OBS profile)
roster.xlsx       participants (optional)
audio/            music files the items play (optional; copied in from the Plan tab)
groups.yaml       breakout groups (optional)
theme.css         per-session stage theme override (optional; applied after the stage lettering)
live/             chat.jsonl, events.jsonl, quiz.jsonl, captures/ — append-only during the session
exports/          session.pdf, report.xlsx, zoom-reconciliation.json, zoom-rooms-*.csv
```

The Sessions tab creates, duplicates (plan, slides, roster, theme, music — never live data) and adds existing folders, and shows a readiness checklist. **Files offline** checks OneDrive's file attributes without downloading anything and can pin the folder ("Always keep on this device"). Unknown keys in `session.yaml` survive a load → save round-trip. An item's `id` is letters, digits, `-` and `_` (up to 80 — it names the item's capture files); one written by hand outside that is made to fit on load (`act Q1` → `act-Q1`, logged ⚠️), and an empty or repeated one gets a fresh id. No database ever lives in the session folder.

## Layout

```
app/
  webapp/            FastAPI server (server.py), routers/, static/ (app, presenter, stage)
  player/            the public quiz player app on 127.0.0.1:8451 (only /play*; published by Tailscale Funnel)
  activities/<type>/ activity plug-ins (editor.json; parse.py + stage.js from step 7)
    static/_vendored/  fleet UI components, vendored verbatim from project-scaffolding
  tray/              pystray tray owning the server (single_instance + watchdog vendored)
src/                 config, logger, build identity, certs, sessions/, importer/, live/, chat/, geo/, groups/, obs/, music/, quiz/, results/
themes/              stage themes (the stage follows these, not the fleet design)
scripts/             verify-before-ship.ps1, gen_icons.py, build_sprite.py, gen_tailscale_cert.py, spotify_login.py
brand/               the Lucide `presentation` master (icons via project-scaffolding's brand_gen)
tests/               hermetic unit tests + tests/e2e (Playwright, disposable instance)
```

## Verify

```powershell
& .\scripts\verify-before-ship.ps1
```

Personal-data guard → byte-compile → ruff → pytest → diff-routed Playwright e2e against a disposable instance.

## Privacy

The repository is public and holds no personal data: sessions, rosters, decks and chat logs live in session folders outside the repo, and tests use synthetic fixtures only.

## Credits

- Cities and countries on the map: [GeoNames](https://www.geonames.org) (`cities15000`, `countryInfo`), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The data is compacted by `scripts/build_geo.py`.
- Country outlines: [world-atlas](https://github.com/topojson/world-atlas) from [Natural Earth](https://www.naturalearthdata.com), public domain.
- Country names in Spanish and English: [Unicode CLDR](https://cldr.unicode.org) through `babel` (build time only).
- Stage lettering: [Patrick Hand](https://fonts.google.com/specimen/Patrick+Hand), SIL Open Font License (`app/webapp/static/fonts/OFL-PatrickHand.txt`).
- Icons: [Lucide](https://lucide.dev), ISC license.

The same credits are in the app under Settings.

## License

MIT. Icons: [Lucide](https://lucide.dev) (ISC).
