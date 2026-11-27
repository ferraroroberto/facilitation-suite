# Install, update and migrate facilitation-suite

facilitation-suite installs, updates and migrates the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. A migration, onto a fresh clone on the same PC or on another one, is one `setup.bat` call. None of them ever resets or overwrites what a PC has configured.

## Install

You need:

- Windows 10 or 11, `git`, and Python 3.14 (the `py` launcher, or `python` on `PATH`). `setup.bat` builds `.venv` with the newest Python the `py` launcher finds, and stops before installing anything when that one (or an existing `.venv`) is older than 3.14.
- PowerPoint desktop (slide import).
- Zoom desktop, or Microsoft Teams or Google Meet in the browser (the chat the activities read: [The chat reader](chat-reader.md)).
- Optional: OBS Studio 28+ with obs-websocket enabled (scene switching), Tailscale (the phone remote over HTTPS), cloudflared (the public quiz), a Spotify Premium account (item music from Spotify).

```powershell
git clone https://github.com/ferraroroberto/facilitation-suite.git
cd facilitation-suite
.\setup.bat
.\tray.bat                      # then open http://127.0.0.1:8456/
```

`setup.bat` runs `src/installer.py`, which:

1. creates `.venv` when it is missing;
2. installs `requirements.txt`, and later reinstalls only when that file changed (its hash is kept inside `.venv`);
3. downloads Playwright's Chromium (the frozen captures and the session PDF render in it), again only when `requirements.txt` changed;
4. creates `config/config.json` from its sample and `.env` from `.env.example` when they are missing;
5. ends with two lists: the required items, each `ok`, `missing` or `unknown`, and the optional features, each `available`, `off` or `unknown` with what it unlocks. Setup has succeeded when it ends with `Setup complete` and exits 0; an optional feature that is `off` never changes that.

`.\setup.bat -WhatIf` lists every change it would make and makes none.

Then set `session_root` (the folder new sessions go under) and, with OBS, its password in `config/config.json` or in the app's Settings ([Configuration](configuration.md)). Keep session folders outside the app folder.

The optional features that may read `off`:

- **Sessions ledger:** `off` until you add your first session; the app creates it then.
- **HTTPS certificate** (phone remote only): `& .\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py`, then `.\tray.bat --restart` ([HTTPS](operations.md#https)).
- **Quiz tunnel config** (public quiz only): the one-time setup in [Quiz player](quiz-player.md).
- **Tailscale and cloudflared** (the same two optional parts): install the one you want.

Two things the checklist does not cover:

- **Spotify** (optional): `.env` is created empty; fill it as in [Spotify setup](music.md#spotify-setup-once).
- **Start at log on:** this app registers nothing itself, so `.\setup.bat -Startup` only reports that. Press <kbd>Win</kbd>+<kbd>R</kbd>, run `shell:startup` and put a shortcut to `tray.bat` in that folder.

A block this PC cannot or will not run can be switched off in [Settings → Available features](settings.md#available-features).

### Uninstall

1. Delete the shortcut to `tray.bat` in `shell:startup`.
2. Quit the tray from its icon. Quit leaves a server the tray did not start itself, so check that nothing is listening; this should print nothing, and `OwningProcess` in a line it does print is the id of the process to end:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8449,8451,8456 -ErrorAction SilentlyContinue
   ```

3. What the app uses outside its folder, each only if you no longer want it:
   - the usage log, in the `facilitation-suite` folder of the runtime-data root (`C:\sqlite\facilitation-suite\` by default);
   - the quiz tunnel: `cloudflared tunnel delete facilitation-quiz`, its DNS record in Cloudflare and its credentials file under `%USERPROFILE%\.cloudflared\`, only when no other PC serves it;
   - the Spotify app's access, removed from your Spotify account page.
4. Delete the folder. Keep a copy of the files listed under "Migrate" first if you may want them back. Session folders outside it stay.

## Update

```powershell
git pull --ff-only
.\setup.bat
.\tray.bat --restart
```

Every file a run writes is gitignored and no tracked file is rewritten at runtime, so the pull always fast-forwards. `setup.bat` on a configured PC is safe, and it is how a new requirement or setting arrives:

- `config/config.json`: an existing value is never changed. A key the sample has and the file lacks is added, after a timestamped backup beside it (`config.json.bak-<YYYYmmdd-HHMMSS>`, gitignored; it holds the same secrets as the config), and the added key names are printed. A file that does not parse is left alone and reported, and the run exits 1.
- `.env` is created when missing and never rewritten. `sessions.local.yaml` and `webapp/cloudflared.yml` are yours alone: `setup.bat` never creates or touches them.
- A second run reports `unchanged` and writes nothing.

## Migrate

What moves is declared in `[install.migrate]` of `.fleet.toml`, each file only when the old copy has it:

| File | What it holds |
|---|---|
| `config/config.json` | this PC's settings, the OBS password and the phone remote token |
| `.env` | the Spotify client id and login |
| `sessions.local.yaml` | the list of your sessions |
| `webapp/certificates/` | the HTTPS certificate and its key |
| `webapp/cloudflared.yml` | the quiz player's tunnel |
| `data/live.json` | which session was live, so the new copy resumes it |
| `data/spotify-names.json` | the names shown for Spotify links |
| `data/replay/` | outputs of past replay runs |

`.venv`, `data/logs/`, `data/tmp/` and the other logs are not copied: they are built again. The usage log is not in the folder: it lives in the `facilitation-suite` folder of the runtime-data root, where the new copy reads it. The installer does the whole move; nothing here or in `setup.bat` deletes the old copy.

**Before you start:** end any live session, and move any session folder or `session_root` that sits **inside** the app folder out of it, fixing its path in `sessions.local.yaml` or `config/config.json`. Neither is copied.

**Reinstall in the same folder (the usual case).** From a terminal whose current folder is outside the app, after `git pull --ff-only` in the app so it has the current `setup.bat`:

```powershell
cd <PARENT FOLDER>
.\facilitation-suite\setup.bat -InPlace -WhatIf    # read the backup name and every step; nothing changes
.\facilitation-suite\setup.bat -InPlace
```

It stops the app, keeps the replaced copy as `facilitation-suite.bak-YYYYMMDD-N`, clones this folder's `origin` into the same path and passes only when the new clone serves its own commit on `/api/version`. The path is unchanged, so the `shell:startup` shortcut needs no edit.

**Beside it (another folder, or an old copy that is not a git clone):**

```powershell
git clone https://github.com/ferraroroberto/facilitation-suite.git <NEW FOLDER>
cd <NEW FOLDER>
.\setup.bat -MigrateFrom <OLD FOLDER> -StopOld
```

`-StopOld` is needed on the same PC: two copies cannot both run there (one tray lock, one port).

Only you can do these steps:

- **Move what Git keeps locally**, if you work in this clone: local branches, stashes and ignored files not listed above stay in the old copy, and a linked worktree stops working once its main folder is renamed. Finish or remove worktrees before you start.
- **On another PC**, copy the old folder over first and use `-MigrateFrom`. What lives outside the app folder does not travel with it: bring the session folders and their `_library`, the tunnel credentials under `%USERPROFILE%\.cloudflared\` and, if you want its history, the usage log. The HTTPS certificate names the old PC, so issue a new one ([HTTPS](operations.md#https)).
- **A usage log still at `data/usage.sqlite3`** in the old folder is not copied. Copy it to the runtime-data folder only if no `usage.sqlite3` is there.
- **Repoint** whatever stores the old path, after a `-MigrateFrom` into another folder (the `shell:startup` shortcut to `tray.bat`, a launcher's app entry): the run lists what still points at it.
- **Check it:** your sessions are listed and open, and Settings shows your values. Try what you use: the phone remote, the quiz link, music.
- **Fall back** if verify fails (the report names the step): stop the new copy, rename the new folder away and the backup to the original name, and start it; it is untouched.
- **Retire the backup** once you are sure the new copy works, by deleting it yourself. An agent working through this playbook asks you before it deletes anything.
