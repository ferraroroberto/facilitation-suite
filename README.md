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
    static/_vendored/  fleet UI components, vendored verbatim from project-scaffolding
  tray/              pystray tray owning the server (single_instance + watchdog vendored)
src/                 config, logger, build identity, certs (non-UI Python)
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
