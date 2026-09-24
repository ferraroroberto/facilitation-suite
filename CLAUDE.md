# Project Instructions — facilitation-suite

Claude Code reads this file as project memory; other agents reach it via the `AGENTS.md` pointer.

> Universal dev-workflow directives live once in `~/.claude/CLAUDE.md` and are not restated here. Fleet-wide *shape* conventions for a FastAPI + static PWA + tray app (visual identity, vendored components, event-loop pinning, `CREATE_NO_WINDOW`, tray self-heal, e2e routing) are owned by `project-scaffolding`'s `CLAUDE.md`. Read `README.md` first.

## This repository

One local app for running a live online workshop: a **stage** (full-screen on the display OBS captures), a **presenter** cockpit (second monitor), Zoom-chat-driven activities read locally through Windows accessibility (no bot), breakout groups, per-item timers and session results. FastAPI + vanilla JS + pystray tray, port **8449**. The epic issue (#1) is the design; each build step is a sub-issue ("Step N/14").

**Project specifics:**

- **Stack is fixed:** Python 3.14 (`py -m venv .venv`; invoke `& .\.venv\Scripts\python.exe`, never activate), FastAPI + uvicorn (selector loop), Pydantic v2, PyYAML, vanilla JS ES modules (no bundler), pystray. Windows-first: PowerPoint COM import and the MSAA chat reader need Windows + PowerPoint + Zoom desktop.
- **Public repo, personal project.** No client, cohort, participant or employer names anywhere — code, docs, issues, commits, screenshots, fixtures. Synthetic data only (`tests/fixtures/`).
- **Personal data never gets committed.** Sessions live in their own folders **outside** the repo (default under OneDrive, `config.session_root`). The ledger `sessions.local.yaml`, `config/config.json`, `data/`, `.env`, any `.xlsx`/`.pptx`/`.docx`, `session.yaml` and `chat.jsonl`/`events.jsonl` outside `tests/fixtures/` are gitignored and refused by the gate's personal-data guard. Run `git status` before every commit.
- **No database inside OneDrive.** Session data is plain files: `session.yaml` + append-only JSONL. Anything cache-like goes under the repo's gitignored `data/`.
- **Visual identity:** the app and the presenter follow the fleet design (`~/.claude/design.md` + `design.dark.md`): tokens in `app/webapp/static/css/tokens.css`, components vendored byte-for-byte from `project-scaffolding` into `app/webapp/static/_vendored/` and recorded in `.fleet.toml [vendored]` — never edit a vendored file. The Lucide sprite is `static/sprite.html`, built by `scripts/build_sprite.py` and inlined into every page at `<!--SPRITE-->`. **The stage does not** follow the fleet design: it follows the session theme (`themes/*.css`), background `#F2F2F2` to match the slides.
- **Env overrides** (tests and the e2e instance use them): `FS_CONFIG_PATH`, `FS_LEDGER_PATH`, `FS_DATA_DIR`.
- **Verification — the pre-ship gate is `& .\scripts\verify-before-ship.ps1`** (personal-data guard → byte-compile → ruff → pytest → routed e2e). The e2e phase boots its own disposable webapp on a free port with temp config/ledger/data; it never touches the live `:8449`. `FS_E2E_LIVE=1` is the one loudly-named opt-in to act (read-only) on the live instance. Keep the e2e suite under 15 collected tests.
- **Restart recipe (long-lived process, no hot-reload):** after the gate passes, `tray.bat --restart` (the shared `%USERPROFILE%\.claude\tray\tray_lifecycle.ps1`: kills the tray subtree, reclaims `:8449` by PID scoped to this repo's `.venv`, starts fresh). **Build-identity check: `GET http://127.0.0.1:8449/api/version` → `git_sha == git rev-parse --short HEAD`**, polled bounded (≤30 s). The chat reader runs as its own process under the tray (step 6) and restarts with it.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`).*

- design spec applies: yes (the app and the presenter; the stage follows the session theme)
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:
  - /           (Sessions · Plan · Groups · Results tabs, Settings behind the header gear)
  - /presenter  (the live cockpit)
  - /stage      (what Zoom sees — session theme, not the fleet design)

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) — hand-authored Mermaid of this repo's structure. Update it in the same PR as any material structural change (a new route family, process, or `src/` package).
