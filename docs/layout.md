# Layout

Where code goes. The structure ratchet (`scripts/structure_ratchet.py`, `.fleet.toml [structure_ratchet]`) enforces this page: a file stays under 1,000 lines, a function under 100, a directory under 40 modules, `src/` never imports `app/`, and the root folders match the declared shape. The offender that predates the ratchet is held in `.structure-ratchet-baseline.json` at its size and may not grow; new code goes where this page says instead.

The shape is `webapp-tray` (`.fleet.toml`): a FastAPI web app with a pystray tray, UI under `app/`, logic under `src/`.

## Root folders

| Folder | What lives there |
|---|---|
| `app/` | everything that serves or shows UI: the webapp, the public quiz player, the activity plug-ins, the tray |
| `src/` | logic with no UI imports: config, sessions, import, live state, chat, quiz, results |
| `tests/` | hermetic unit tests, plus `tests/e2e/` (Playwright against a disposable instance) |
| `docs/` | durable reference, one page per feature |
| `scripts/` | the gate (`verify-before-ship.ps1`), the vendored ratchets, their gate wrappers and the runner they share (`ratchet_gate.py`), and one-off tools |
| `assets/` | the tray icon and the Stream Deck icon |
| `config/` | `config.sample.json`, the committed template; the live `config.json` next to it is gitignored |
| `brand/` | the Lucide `presentation` master the icons are generated from |
| `themes/` | declared exception, see below |
| `webapp/` | declared exception, see below |

## Declared exceptions

Both are listed in `extra_roots` in `.fleet.toml`.

- **`themes/`** holds the stage themes (`default.css` and any added next to it). The stage follows the session theme, not the fleet design, and the server stamps these files with the same content hash as the webapp's static files. A theme is a stylesheet picked by name from the session, so it sits beside the app rather than inside `app/webapp/static/`, which holds the fleet-design pages.
- **`webapp/`** is the machine-local folder the server and the tunnel read at runtime: the HTTPS certificates and `cloudflared.yml`. Only `cloudflared.sample.yml`, the template, is tracked; the rest is gitignored. The path is part of the tray's and the tunnel's config contract, so it stays a root folder.

## `app/`

| Package | What lives there |
|---|---|
| `app/webapp/` | the FastAPI server (`server.py`), `routers/`, and `static/` (the Sessions/Plan/Groups/Results page, `/presenter`, `/stage`, `/remote`) |
| `app/webapp/static/js/` | ES modules, no bundler; `views/` holds one module per tab |
| `app/webapp/static/_vendored/` | fleet UI components copied byte-for-byte from project-scaffolding; never edited here (see `.fleet.toml [vendored]`) |
| `app/player/` | the public quiz player on `127.0.0.1:8451`, serving only `/play*` |
| `app/activities/<type>/` | one folder per activity plug-in (word cloud, scale, map, cards, feed, quiz screens): editor definition, parser, stage renderer |
| `app/tray/` | the pystray tray that owns the server; `single_instance.py` and `watchdog.py` are vendored |

## `src/`

| Module or package | What lives there |
|---|---|
| `config.py`, `defaults.py`, `settings.py`, `library.py` | configuration, what new sessions start from, the Settings model, stage fonts and themes |
| `sessions/` | the session folder: model, store, readiness, theme, offline use |
| `importer/` | PowerPoint import through COM and the re-import review |
| `live/` | live state: the plan clock, capture, freeze, the hub, actions |
| `chat/` | the chat reader (Zoom through MSAA, Teams and Meet through UI Automation) and answer parsing |
| `activities/` | the activity registry and the answer handling the plug-ins share |
| `groups/` | roster and shuffle |
| `obs/` | obs-websocket scene switching |
| `music/` | files and Spotify per item |
| `quiz/` | the quiz engine, importer, scoring, cues, reachability |
| `results/` | collection, reconciliation, PDF and Excel exports |
| `geo/` | the offline gazetteer behind the map |
| `no_window.py`, `build_info.py`, `usage_log.py` | vendored from project-scaffolding |
| the other top-level modules | small shared helpers: errors, files, JSONL, logging, env file, certificates, the tunnel |

## Where new code goes

- A new feature's logic goes in a `src/<domain>/` package, and its HTTP surface in a module under `app/webapp/routers/`. `src/` never imports `app/`.
- A new directory-level helper goes in the domain package that uses it, not at the top of `src/`.
- Browser code goes in `app/webapp/static/js/`; a view's helpers go in a module beside it, not into the view file.
- Tests mirror the code they cover. A unit test for a domain with its own folder (`tests/quiz/`) goes there; an end-to-end story goes in `tests/e2e/`. A long story is a short test that calls named `_step` helpers.
- A file or function over the limit is split along the seam its own sections already show. The one baselined offender, `createStage` in `stage-render.js`, keeps its build, update and draw steps in one closure over shared state, so splitting it means moving that state into an object; that change is made on its own, never as a side effect.
