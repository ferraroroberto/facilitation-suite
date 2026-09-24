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

Open `http://127.0.0.1:8449/` for the app, `/presenter` on the second monitor, and `/stage` full-screen (F11) on the display OBS captures.

**Restart matrix:** anything under `app/` or `src/` → `tray.bat --restart`. Static files (`app/webapp/static/`) are served `no-cache`, so a browser reload picks them up without a restart.

## Importing a PowerPoint

Sessions → **Import PowerPoint** (type the path or **Browse**, which opens the Windows file dialog on this PC). PowerPoint desktop exports every slide to a 1920×1080 PNG through COM, from a read-only copy of the deck, in its own process with a 5-minute timeout. Each slide keeps PowerPoint's own SlideID, title, notes and fingerprints (image dHash + title/notes hashes) in `slides/slides.json`.

- **OBS profile per slide** comes from where the slide reserves the camera: a flat grey box (or empty area) on the right → *Camera strip*; a small box top-right → *Camera PiP*; nothing reserved → *Screen only*. A notes line `[obs: pip|strip|screen]` overrides it; an unsure slide stays unset and is flagged.
- **First import builds the plan:** PowerPoint sections when the deck has them, otherwise a new section at every title-only divider slide. Placeholder slides become plan items: a slide titled `activity – <question>` (or the older `streamalive N – <question>`) becomes an activity (map / scale / word cloud guessed from the words), `breakout – <title>` a break, `mentimeter …` a skipped slide.
- **Re-import** matches slides by SlideID: your plan order, activities and timers stay; removed slides drop out; new slides are inserted after the slide that precedes them in the deck.

`FS_TEST_POWERPOINT=1` runs the one test that drives the real PowerPoint (on a synthetic deck it builds itself).

## Planning

The **Plan** tab edits `session.yaml`: sections with planned minutes (drag to reorder, rename, collapse), and inside each section the slides, activities and breaks in order (drag, or Alt+↑/↓, or Move up/down). Each item has:

- an **OBS profile** (Camera strip / Camera PiP / Screen only; slides start with the detected one),
- its **own timer** — no global defaults, decided item by item: duration, when it starts (manually, when the item opens, with the capture), where it shows (stage / presenter / both) and what happens at 00:00 (keep, stop the capture, next item, chime),
- **In this session** (off = skipped live, kept in the plan),
- for activities: type, question, question font and size (in stage pixels on the 1920×1080 canvas), the prompt to paste in the chat, and the type's own answer options.

Activity types are plug-ins: one folder per type under `app/activities/<type>/` with an `editor.json` (label, icon, options). Edits are staged; **Save to session.yaml** is the only write.

## Presenting live

**Open presenter** (Sessions tab) makes the session live and opens `/presenter` on the second monitor; **Open stage window** opens `/stage` — drag it to the display OBS captures and double-click it for full screen. Both follow one state owned by the server and pushed over the `/ws` WebSocket: every view only sends *intents* (next, blackout, timer…), so the stage and the presenter can never disagree.

| Key (stage or presenter window) | Action |
|---|---|
| → · PageDown · ↓ · N | next item (a presentation clicker works) |
| ← · PageUp · ↑ · P | previous item |
| B · . | blackout |
| T | the item's timer: start / pause |
| M · + | the item's timer: +1 min |
| Space | capture start / stop (activities) |

The presenter shows what is on stage, the next item and the three after it **by title**, the speaker notes (and, for an activity, the prompt to paste in the chat with a Copy button), the item's timer controls, and the presenter-only clocks: the session clock against the planned duration (ahead / behind), the time left in the current section and when the next break is due. Click any thumbnail in the filmstrip to jump there.

The live position, clocks and timers are mirrored to `live/state.json` (a restarted server resumes where it was) and every item change, clock and timer event is appended to `live/events.jsonl`. Saving the plan while live reloads it in place. The stage's look comes from `themes/default.css` plus the session's own optional `theme.css`; the hint under activities ("Write your answer in the Zoom chat") is set per session in **Session details**.

## Activities and captures

On an activity, **Space** (or the presenter's big button) starts the **capture**: every participant message that arrives from then on belongs to the activity, and the stage grows its visual live — a word cloud, scale bars with the average, cards, or a feed of bubbles. Space again stops it; a stopped capture can be reopened (what arrived while it was stopped stays out). Only one capture runs at a time. Your own messages ("You" in Zoom) never count. **Click a message** in the presenter's chat to hide it from the activity (a "can you hear me?"); click again to count it back.

At every stop the result is **frozen** in the session folder: `live/captures/<item>.json` (every answer with its name and parsed value, and the result) and `live/captures/<item>.png` (the stage as it looked, rendered by a headless browser). An item timer set to start *with the capture* starts with it; one set to *stop the capture* at 00:00 does.

| Type | What counts | On the stage |
|---|---|---|
| Word cloud | answers of up to three words stay one phrase; longer ones become words minus filler words (Spanish/English lists); laughter dropped; case, accents and simple plurals merged | the cloud grows, most frequent biggest |
| Scale | the first number in range, or a keyword from the list (lowest → highest); one vote per person | bars with counts, the leader highlighted, the average |
| Map | a place, geocoded offline: `Milan, italy`, `Sevilla, España`, `CDMX`, `desde Bogotá`, a country alone (→ its capital); an ambiguous city follows the room ("Valencia" goes to Spain when the others are there); one pin per person, their latest answer | people pop in with their names, the view fits everyone, a crowded region gets its own inset, click a pin for who is there; the headcount, countries and top cities alongside |
| Cards | each answer with its name | the latest cards in a grid |
| Feed | each answer with its name | bubbles, the newest at the bottom and largest |

Each type is a plug-in folder under `app/activities/<type>/`: `editor.json` (the Plan tab's fields and sample answers), `parse.py` (`parse(message, options)` → contribution, `aggregate(contributions, options)` → result; pure and unit-tested) and `stage.js` + `stage.css` (draws a result). The Plan tab's stage preview is drawn by the same renderer with the sample answers, and the presenter's *Simulate answers* on an activity uses them too.

**Map answers that land nowhere** are listed on the presenter under *Not on the map* with the closest places as one-click buttons, or type the place and press Enter. Common chat spellings live in `app/activities/map/aliases.yaml` (add a line when an answer keeps landing there). The map's data is built once by `scripts/build_geo.py` (needs the network and `babel`) and committed; the app never downloads anything.

## Breakout groups

The **Groups** tab makes the 1-2-4-all breakout rooms (the algorithm ported from `facilitation-shuffle`, unchanged):

1. **Import roster (.xlsx)** — the first sheet, columns by header in any order: `name` (required; without a `name` header the first column is used), `role`, `company`, `country`, `present`, `email` (optional). The file is copied into the session folder as `roster.xlsx`; the original is never edited.
2. **Mark who is here** with the switches (they win over the file's `present` column and are saved in `groups.yaml`).
3. **Shuffle** — three rounds: **pairs** (an odd number gives one trio), **groups of 4 · A** (whole pairs merged, never split; 4k+2 people give one room of six), **groups of 4 · B** (re-mixed into rooms of four — a room of three or five takes the remainder, never fewer than three — keeping as few people as possible with someone from their round-A room — up to 120 000 tries). The note under the tabs says how well round B mixed.
4. **Copy rooms for Zoom** (`Room 1: Ana, Sam` lines to paste while assigning rooms by hand) or **Export Zoom pre-assign CSV** (`Pre-assign Room Name,Email Address`, also kept in `exports/`) — only when everyone present has an email; otherwise the tab says who is missing one.
5. **Add reveal slide** puts a "Who are you with?" item at the end of the first section (move it in the Plan tab): the stage shows every room of that round, numbered.

If presence changes after a shuffle, the tab and the readiness checklist say so until you shuffle again.

## OBS

Each item in the plan has an **OBS profile** — *Camera strip*, *Camera PiP* or *Screen only*. When an item goes on stage the app switches OBS to that profile's scene over **obs-websocket v5** (built into OBS 28+; OBS → Tools → WebSocket Server Settings, default port 4455), and the stage keeps the profile's camera zone empty. **Settings → OBS profiles** picks each profile's scene from the scenes OBS has (or type one while OBS is closed), moves the camera zones, and holds the connection (host, port, password — the password is written to `config/config.json` and never shown again). The `obs_profile/<name>` action switches by hand (Stream Deck).

OBS is never in the critical path: it runs on its own thread, reconnects by itself, and the presenter's **OBS** chip says *profile …*, *not reachable* (click to retry), *connecting* or *off*. A profile without a scene, or a scene OBS doesn't have, leaves OBS as it is and the chip says so. The deck keeps working with OBS closed.

## The Zoom chat reader

Participants answer in the Zoom chat; a separate local process reads it — no bot, no Zoom app. In Zoom, **pop out the meeting chat** (Chat → … → Pop out): the reader finds that window (`Meeting chat`) and reads every message through Windows accessibility (MSAA) twice a second, then posts the new ones to the server, which appends them to the session's `live/chat.jsonl` and shows them on the presenter.

- It starts when a session goes live (`reader.enabled` in the config), from the presenter's **Zoom chat** chip or card, or from the readiness **Test** in the Sessions tab. It stops with the live session and exits by itself if the server goes away.
- The presenter chip says what it is doing: *reading*, *pop out the chat* (window not found), *no answer* (no heartbeat for 3 s), *error*, *simulating* or *off*.
- Messages already in the window when it starts are history (Zoom keeps chat across meetings in the same room), so a restarted reader never duplicates what was stored. Emoji arrive as nothing, so prompts should ask for words or numbers. Private messages are not read.
- While it runs, the Windows "screen reader present" flag is on (Zoom may need it to expose the chat); the reader restores it when it stops.
- **Rehearse alone:** *Simulate answers* on the presenter replays fake answers through the same pipeline; from a terminal, `python -m src.chat.reader --server http://127.0.0.1:8449 --simulate burst:50` (or `random:30`, or a YAML script `{messages: [{after, sender, text}]}`).

Log: `data/logs/chat-reader.log`.

## Configuration

`config/config.json` (gitignored; `config/config.sample.json` documents every key):

| Key | Meaning |
|---|---|
| `port` | server port (8449) |
| `session_root` | default parent folder for new sessions (`<root>\<workshop>\<session>\`) |
| `stage_display` | which display the stage window goes on (informational) |
| `obs` | obs-websocket host / port / password (the password stays in this file only), `enabled` = scene switching on/off |
| `profiles` | the three OBS profiles: each one's OBS `scene` and the camera `zone` the stage keeps empty (`[left, top, right, bottom]` as fractions, `null` = no camera) |
| `reader` | Zoom chat reader: poll interval and the chat window's class and title |

The **ledger** `sessions.local.yaml` (gitignored; example in `sessions.example.yaml`) lists session names and folders only. Each session lives in its own folder with its own `session.yaml`.

## Sessions

A session is a folder, by default `<session_root>\<workshop>\<session>\`:

```
session.yaml      the plan (schema v1) — human-readable, safe to edit by hand
slides/           slide PNGs + slides.json (titles, notes, fingerprints, detected OBS profile)
roster.xlsx       participants (optional)
groups.yaml       breakout groups (optional)
theme.css         per-session stage theme override (optional)
live/             chat.jsonl, events.jsonl, captures/ — append-only during the session
exports/          session PDF, Excel report, Zoom rooms CSV
```

The Sessions tab creates, duplicates (plan, slides, roster, theme — never live data) and adds existing folders, and shows a readiness checklist. **Files offline** checks OneDrive's file attributes without downloading anything and can pin the folder ("Always keep on this device"). Unknown keys in `session.yaml` survive a load → save round-trip. No database ever lives in the session folder.

## Layout

```
app/
  webapp/            FastAPI server (server.py), routers/, static/ (app, presenter, stage)
  activities/<type>/ activity plug-ins (editor.json; parse.py + stage.js from step 7)
    static/_vendored/  fleet UI components, vendored verbatim from project-scaffolding
  tray/              pystray tray owning the server (single_instance + watchdog vendored)
src/                 config, logger, build identity, certs, sessions/, importer/, live/ (hub, actions)
themes/              stage themes (the stage follows these, not the fleet design)
scripts/             verify-before-ship.ps1, gen_icons.py, build_sprite.py, gen_tailscale_cert.py
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
