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

## Configuration

`config/config.json` (gitignored; `config/config.sample.json` documents every key):

| Key | Meaning |
|---|---|
| `port` | server port (8449) |
| `session_root` | default parent folder for new sessions (`<root>\<workshop>\<session>\`) |
| `stage_display` | which display the stage window goes on (informational) |
| `obs` | obs-websocket host / port / password (the password stays in this file only) |
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

## License

MIT. Icons: [Lucide](https://lucide.dev) (ISC).
