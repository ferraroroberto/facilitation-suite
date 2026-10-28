# facilitation-suite

One local app for running a live online workshop:

- a **stage** (a full-screen browser window on the display OBS already captures, so Zoom sees it through the OBS virtual camera),
- a **presenter** cockpit on the second monitor (on stage now, what comes next by title, notes, clocks, the live Zoom chat),
- **activities fed by the Zoom chat**: participants just type in the chat and the stage comes alive (word cloud, map, scale, cards, feed), read locally through Windows accessibility, with **no bot joining the meeting**,
- **breakout groups** (pairs, then two rounds of four with no repeats), per-item **timers**, and **results** after the session.

Slides stay authored in PowerPoint and are imported as images. Everything runs on one Windows PC; nothing leaves it except Zoom itself.

## Portable install

A clean clone runs the same on any Windows PC: nothing outside the repo and the tools below is needed, and no other project has to be installed.

**Prerequisites**

- Windows 10/11, Python 3.14 (the `py` launcher), Git
- PowerPoint desktop (slide import through COM)
- Zoom desktop with the meeting chat **popped out** (the chat reader targets that window) — or Microsoft Teams in the browser with the meeting chat open (see [Teams instead of Zoom](docs/chat-reader.md#teams-instead-of-zoom)), or a Google Meet tab with its chat panel open (see [Google Meet](docs/chat-reader.md#google-meet))
- Optional: OBS Studio 28+ with obs-websocket enabled (scene switching per item), Tailscale (the phone remote over HTTPS), cloudflared (the public quiz), and a `.env` with the Spotify login (item music from Spotify)

**Steps**, from the repo root in PowerShell:

```powershell
py -3.14 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m playwright install chromium
copy config\config.sample.json config\config.json   # then edit session_root, OBS password
.\tray.bat                                           # then open http://127.0.0.1:8456/
```

Never activate the venv; invoke its interpreter directly. Playwright's Chromium renders the frozen captures and the session PDF. `tray.bat` and `tray.bat --restart` need nothing but the repo's own `.venv`; when the fleet's shared tray helper happens to be installed they use it, and the result is the same.

**What stays on the PC and is carried over by hand** (none of it is in the repo): the session folders (`session_root` in `config/config.json`, default under OneDrive), the ledger `sessions.local.yaml` that lists them (shape: `sessions.example.yaml`; the app creates an empty one), `config/config.json`, `.env`, and the Tailscale certificate and cloudflared config under `webapp/`. Copy the folders and the ledger, fix the paths in the ledger, and the sessions open as they were. Settings: [docs/configuration.md](docs/configuration.md).

**Developer-only tools** keep their own needs and are not part of running the app: `scripts/gen_icons.py` needs a project-scaffolding checkout, and the pre-ship gate (`scripts/verify-before-ship.ps1`) needs Node for its duplicate-block check.

## Run

| Command | What |
|---|---|
| `tray.bat` | tray icon that owns the server on **:8449** (and the loopback-only listener on **:8456**; idempotent; put it in the Startup folder) |
| `tray.bat --restart` | orphan-proof restart; verifies the served build (`/api/version`) matches `HEAD`; without the shared tray helper it ends only this app's own tray and server and starts them again |
| `webapp.bat` | foreground server (dev / headless) |

Open the app, `/presenter` on the second monitor, and `/stage` full-screen (F11) on the display OBS captures — on this PC at `http://127.0.0.1:8456/` (the tray's **Open**: plain HTTP on loopback only, so no certificate warning and no tailnet hop). Other devices (the phone remote, a Stream Deck) use `https://<this PC>.<tailnet>.ts.net:8449/` once [HTTPS](docs/operations.md#https) is set up, or `http://127.0.0.1:8449/` while it is not. This PC never needs a token either way.

Appearance (light, dark or system), HTTPS for the phone remote and the log files: [docs/operations.md](docs/operations.md).

**Restart matrix:** anything under `app/` or `src/` → `tray.bat --restart`. The `.js`/`.css` under `app/webapp/static/`, `themes/` and `app/activities/` are stamped with one hash taken at startup and cached for good under it (the pages stay `no-cache`), so an edit to them shows up after the restart, not on a reload.

## Features

Each feature has its own reference in `docs/`:

- **Importing a PowerPoint** — slides exported as images with their text, notes and camera layout; re-import the whole deck or the selected slides, as a reviewed diff: [docs/import.md](docs/import.md)
- **Planning** — the Plan tab, per-item timers, stage lettering and timer colours: [docs/planning.md](docs/planning.md)
- **Presenting live** — stage and presenter, keys, restart behaviour: [docs/live.md](docs/live.md)
- **Music** — files and Spotify per item, the Spotify setup: [docs/music.md](docs/music.md)
- **Activities and captures** — word cloud, scale, map, cards, feed: [docs/activities.md](docs/activities.md)
- **Quiz** — the Kahoot-style game, scoring, stage, sound cues: [docs/quiz.md](docs/quiz.md)
- **Quiz player (public)** — the separate phone app and its Cloudflare tunnel: [docs/quiz-player.md](docs/quiz-player.md)
- **Running a quiz** — import to podium, the reachability check, measured limits: [docs/running-a-quiz.md](docs/running-a-quiz.md)
- **Phone remote** — `/remote`, pairing, the token: [docs/phone-remote.md](docs/phone-remote.md)
- **Results and exports** — the Results tab, session PDF, Excel report, chat reconciliation, the session analysis skill and its JSON dump, rebuilding a past session's results with today's rules: [docs/results.md](docs/results.md)
- **Breakout groups** — roster, shuffle, Zoom pre-assign: [docs/groups.md](docs/groups.md)
- **OBS** — camera layouts over obs-websocket: [docs/obs.md](docs/obs.md)
- **Stream Deck** — every live control as one URL: [docs/stream-deck.md](docs/stream-deck.md)
- **The chat reader (Zoom, Teams or Meet)** — how answers are read locally: [docs/chat-reader.md](docs/chat-reader.md)
- **Settings** and **Configuration** — [docs/settings.md](docs/settings.md), [docs/configuration.md](docs/configuration.md)
- **Sessions** — the session folder: [docs/sessions.md](docs/sessions.md)
- **Running and operating** — appearance, HTTPS, logs: [docs/operations.md](docs/operations.md)
- **Verification** — the gate in detail, the design inventory, the verify skill: [docs/verification.md](docs/verification.md)
- **Privacy** — what the repository holds and the local usage log: [docs/privacy.md](docs/privacy.md)

## Layout

Where each folder's code goes, and why `themes/` and `webapp/` sit at the root: [docs/layout.md](docs/layout.md).

```
app/
  webapp/            FastAPI server (server.py), routers/, static/ (app, presenter, stage)
  player/            the public quiz player app on 127.0.0.1:8451 (only /play*; published by the Cloudflare tunnel)
  activities/<type>/ activity plug-ins (editor.json; parse.py + stage.js from step 7)
    static/_vendored/  fleet UI components, vendored verbatim from project-scaffolding
  tray/              pystray tray owning the server (single_instance + watchdog vendored)
src/                 config, defaults (what new sessions start from), library (stage fonts and themes), logger, build identity, certs, tunnel (cloudflared), sessions/, importer/, live/, chat/, geo/, groups/, obs/, music/, quiz/, results/
themes/              stage themes (the stage follows these, not the fleet design)
scripts/             verify-before-ship.ps1, standalone_restart.py, quiz_bots.py, gen_icons.py, build_sprite.py, gen_tailscale_cert.py, spotify_login.py
brand/               the Lucide `presentation` master (icons via project-scaffolding's brand_gen)
tests/               hermetic unit tests + tests/e2e (Playwright, disposable instance)
webapp/              machine-local, gitignored: certificates/, cloudflared.yml (template: cloudflared.sample.yml, committed)
```

## Verify

```powershell
& .\scripts\verify-before-ship.ps1
```

Personal-data guard → byte-compile → ruff → static ratchet → docs ratchet → comment ratchet → structure ratchet → instruction ratchet → doc-refs ratchet → dead-code ratchet → conventions ratchet → pytest → diff-routed Playwright e2e against a disposable instance. Details: [docs/verification.md](docs/verification.md).

## Privacy

The repository is public and holds no personal data: sessions, rosters, decks and chat logs live in session folders outside the repo, and tests use synthetic fixtures only. The local usage log: [docs/privacy.md](docs/privacy.md).

## Credits

- Cities and countries on the map: [GeoNames](https://www.geonames.org) (`cities15000`, `countryInfo`), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The data is compacted by `scripts/build_geo.py`.
- Country outlines: [world-atlas](https://github.com/topojson/world-atlas) from [Natural Earth](https://www.naturalearthdata.com), public domain.
- Country names in Spanish and English: [Unicode CLDR](https://cldr.unicode.org) through `babel` (build time only).
- Stage lettering: [Patrick Hand](https://fonts.google.com/specimen/Patrick+Hand), SIL Open Font License (`app/webapp/static/fonts/OFL-PatrickHand.txt`).
- Icons: [Lucide](https://lucide.dev), ISC license.

The same credits are in the app under Settings.

## License

MIT. Icons: [Lucide](https://lucide.dev) (ISC).
