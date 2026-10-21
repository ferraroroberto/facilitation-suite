# Project Instructions — facilitation-suite

Claude Code reads this file as project memory; other agents reach it via the `AGENTS.md` pointer.

> Universal dev-workflow directives live once in `~/.claude/CLAUDE.md` and are not restated here. Fleet-wide *shape* conventions for a FastAPI + static PWA + tray app (visual identity, vendored components, event-loop pinning, `CREATE_NO_WINDOW`, tray self-heal, e2e routing) are owned by `project-scaffolding`'s `CLAUDE.md`. Read `README.md` first.

## This repository

One local app for running a live online workshop: a **stage** (full-screen on the display OBS captures), a **presenter** cockpit (second monitor), Zoom-chat-driven activities read locally through Windows accessibility (no bot), breakout groups, per-item timers and session results. FastAPI + vanilla JS + pystray tray, port **8449**. The epic issue (#1) is the design; each build step is a sub-issue ("Step N/14").

**Project specifics:**

- **Stack is fixed:** Python 3.14 (`py -m venv .venv`; invoke `& .\.venv\Scripts\python.exe`, never activate), FastAPI + uvicorn (selector loop), Pydantic v2, PyYAML, vanilla JS ES modules (no bundler), pystray. Windows-first: PowerPoint COM import and the MSAA chat reader need Windows + PowerPoint + Zoom desktop.
- **Public repo, personal project.** No client, cohort, participant or employer names anywhere — code, docs, issues, commits, screenshots, fixtures. Synthetic data only (`tests/fixtures/`).
- **Personal data never gets committed.** Sessions live in their own folders **outside** the repo (default under OneDrive, `config.session_root`). The ledger `sessions.local.yaml`, `config/config.json`, `data/`, `.env`, any `.xlsx`/`.pptx`/`.docx`, `session.yaml` and `chat.jsonl`/`events.jsonl`/`quiz.jsonl` outside `tests/fixtures/` are gitignored and refused by the gate's personal-data guard. Run `git status` before every commit.
- **No database inside OneDrive.** Session data is plain files: `session.yaml` + append-only JSONL. Anything cache-like goes under the repo's gitignored `data/`.
- **Visual identity:** the app and the presenter follow the fleet design (`~/.claude/design.md` + `design.dark.md`): tokens in `app/webapp/static/css/tokens.css`, components vendored byte-for-byte from `project-scaffolding` into `app/webapp/static/_vendored/` and recorded in `.fleet.toml [vendored]` — never edit a vendored file. The Lucide sprite is `static/sprite.html`, built by `scripts/build_sprite.py` and inlined into every page at `<!--SPRITE-->`. **The stage does not** follow the fleet design: it follows the session theme (`themes/*.css`), background `#F2F2F2` to match the slides.
- **Env overrides** (tests and the e2e instance use them): `FS_CONFIG_PATH`, `FS_LEDGER_PATH`, `FS_DATA_DIR`, `FS_ENV_PATH` (the `.env` holding the Spotify login).
- **Verification — the pre-ship gate is `& .\scripts\verify-before-ship.ps1`** (personal-data guard → byte-compile → ruff → static ratchet → pytest → routed e2e). The **static ratchet** (`scripts/static_ratchet.py`, vendored; `.fleet.toml [static_ratchet]`, baseline `.static-ratchet-baseline.json`, #289) fails on a duplicate block (jscpd via `npx`, so **Node is a prerequisite**) or an unused import/variable (ruff `F401,F841`) that is not in the baseline; it reads the whole tree, so it is never path-routed, and the gate calls it only through `python -m scripts.static_ratchet_gate`, which fails exit 3 (a detector could not run) as NOT CONFIRMED, never a pass or a skip. Shrink the baseline in the PR that removes debt (`--update-baseline`); adding to it needs `--allow-additions` and shows in review. The e2e phase boots its own disposable webapp on a free port with temp config/ledger/data; it never touches the live `:8449`. `FS_E2E_LIVE=1` is the one loudly-named opt-in to act (read-only) on the live instance. Keep the e2e suite under 15 collected tests. **Runtime contract (measured 2026-10-03, #165):** 14 nodes, one chromium projection; the e2e phase takes about 47–50 s (the first node boots the disposable app in about 3 s; a cold first run after a reboot has cost 15 s more), unit pytest about 2 m 20 s, the whole gate about 3½ min on a `full` tier and about 2½ min when e2e is routed `skip`. **Routing:** `.fleet.toml [e2e]` sends any `src/` change to `full` (the app imports `src/`, #171), so only `tests/` unit files, `scripts/`, `docs/` and other prose skip the browser leg. The e2e phase writes `data/e2e-junit.xml` (`[e2e] junit_xml`), the record `/e2e-audit` reads its timing from; a number here that is more than 25 % off a quiet run is stale.
- **Restart recipe (long-lived process, no hot-reload):** after the gate passes, run `& .\tray.bat --restart` **from a real PowerShell shell** (the shared `%USERPROFILE%\.claude\tray\tray_lifecycle.ps1`: kills the tray subtree, reclaims `:8449` by PID scoped to this repo's `.venv`, starts fresh). Backgrounded, `Start-Process`, or `cmd /c` launches have silently done nothing several times — always run it foreground, in PowerShell, and read its own "Verified ... serves git_sha ..." output. **Build-identity check: `GET https://127.0.0.1:8449/api/version` → `git_sha == git rev-parse --short HEAD`** (`:8449` serves HTTPS via a tailnet cert; use `-k`/no-verify), polled bounded (≤30 s). The chat reader runs as its own process under the tray (step 6) and restarts with it.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`).*

- design spec applies: yes
- scope note: the app and the presenter; the stage follows the session theme
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:
  - /           (Sessions · Plan · Groups · Results tabs, Settings behind the header gear)
  - /presenter  (the live cockpit)
  - /stage      (what Zoom sees — session theme, not the fleet design)

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) — hand-authored Mermaid of this repo's structure. Update it in the same PR as any material structural change (a new route family, process, or `src/` package).

## CI expectations

- Workflow `.github/workflows/ci.yml`, job `gate`, on every PR and every push to `main`. **Advisory, not required** (no branch protection): the local gate (`scripts/verify-before-ship.ps1`) is the contract.
- Typical green: **~6 min** (first run: unit 3m20 + e2e 1m30 + setup; measured 2026-10-18). Investigate at **>12 min**; treat as wedged at **>~24 min**.
- Flaky leg: the desktop chromium e2e leg can slow down on the hosted runner. `timeout-minutes: 25` caps a wedge. A wedge is a flake, not the diff. There is no phone/WebKit leg: the phone viewport is emulated inside chromium by the tests that need it.
- CI runs the unit suite (`pytest --ignore=tests/e2e`) and the e2e suite; it does not run ruff, byte-compile or the personal-data guard (the local gate does). The one test that drives real PowerPoint COM skips itself unless `FS_TEST_POWERPOINT=1`; the fake-Zoom MSAA burst test runs on the runner's desktop.
- CI's only signal beyond the local gate is a clean machine: a fresh `.venv` from `requirements.txt`, committed files only. Its e2e surface = `app/`, `src/`, `themes/`, `tests/e2e/`, `tests/conftest.py`, `tests/fixtures/*.py`, `config/config.sample.json` (`.fleet.toml [e2e]` routes the same paths to `full`). A diff touching **none** of these gains nothing from CI.
