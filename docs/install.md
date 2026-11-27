# Install, update and move facilitation-suite

facilitation-suite moves to another Windows PC like this: clone it and run one `setup.bat`. An update is a pull plus a restart. Neither ever resets or overwrites what a PC has configured.

## What you need

- Windows 10 or 11, `git`, and Python 3.14 (the `py` launcher, or `python` on `PATH`). `setup.bat` builds `.venv` with the newest Python the `py` launcher finds, and stops before installing anything when that one (or an existing `.venv`) is older than 3.14.
- PowerPoint desktop (slide import).
- Zoom desktop, or Microsoft Teams or Google Meet in the browser (the chat the activities read: [The chat reader](chat-reader.md)).
- Optional: OBS Studio 28+ with obs-websocket enabled (scene switching), Tailscale (the phone remote over HTTPS), cloudflared (the public quiz), a Spotify Premium account (item music from Spotify).

## Fresh PC

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

Then set `session_root` (the folder new sessions go under) and, with OBS, its password in `config/config.json` or in the app's Settings ([Configuration](configuration.md)).

The optional features that may read `off`:

- **Sessions ledger:** `off` until you add your first session; the app creates it then.
- **HTTPS certificate** (phone remote only): `& .\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py`, then `.\tray.bat --restart` ([HTTPS](operations.md#https)).
- **Quiz tunnel config** (public quiz only): the one-time setup in [Quiz player](quiz-player.md).
- **Tailscale and cloudflared** (the same two optional parts): install the one you want.

Two things the checklist does not cover:

- **Spotify** (optional): `.env` is created empty; fill it as in [Spotify setup](music.md#spotify-setup-once).
- **Start at log on:** this app registers nothing itself, so `.\setup.bat -Startup` only reports that. Press <kbd>Win</kbd>+<kbd>R</kbd>, run `shell:startup` and put a shortcut to `tray.bat` in that folder.

A block this PC cannot or will not run can be switched off in [Settings → Available features](settings.md#available-features).

## Update

```powershell
git pull --ff-only
.\tray.bat --restart
```

Nothing else: every file a run writes is gitignored and no tracked file is rewritten at runtime, so the pull always fast-forwards. A setting added since your `config/config.json` was written runs on its default. The one exception: when the pull changed `requirements.txt`, run `.\setup.bat` before the restart.

`setup.bat` is safe to run on a configured PC at any time:

- `config/config.json`: an existing value is never changed. A key the sample has and the file lacks is added, after a timestamped backup beside it (`config.json.bak-<YYYYmmdd-HHMMSS>`, gitignored; it holds the same secrets as the config), and the added key names are printed. A file that does not parse is left alone and reported, and the run exits 1.
- `.env` is created when missing and never rewritten. `sessions.local.yaml` and `webapp/cloudflared.yml` are yours alone: `setup.bat` never creates or touches them.
- A second run reports `unchanged` and writes nothing.

## Migrate from an existing install

Use this on a PC that already runs an older copy: one that may not be a git clone, and may live at another path. The old copy keeps running until step 4, and it is deleted only when you say so.

1. **Clone fresh beside it,** into a new folder next to the old one; never clone over the old folder.

   ```powershell
   git clone https://github.com/ferraroroberto/facilitation-suite.git <NEW FOLDER>
   ```

2. **Copy this PC's own files across.** They are all gitignored, so nothing tracked is touched. Skip what the old copy does not have.

   | File | What it holds |
   |---|---|
   | `config/config.json` | this PC's settings, the OBS password and the phone remote token |
   | `.env` | the Spotify client id and login |
   | `sessions.local.yaml` | the list of your sessions |
   | `webapp/certificates/` | the HTTPS certificate and its key |
   | `webapp/cloudflared.yml` | the quiz player's tunnel |
   | `data/live.json` | which session was live, so the new copy resumes it |
   | `data/spotify-names.json` | the names shown for Spotify links |
   | `data/replay/` | outputs of past replay runs, if you want to keep them |

   ```powershell
   $old = "<OLD FOLDER>"; $new = "<NEW FOLDER>"     # both as full paths
   $files = "config\config.json", ".env", "sessions.local.yaml", "webapp\certificates", "webapp\cloudflared.yml",
            "data\live.json", "data\spotify-names.json", "data\replay"
   foreach ($file in $files) {
       $source = Join-Path $old $file
       if (Test-Path $source) {
           $folder = Split-Path (Join-Path $new $file)
           New-Item -ItemType Directory -Force $folder | Out-Null
           Copy-Item $source $folder -Recurse -Force
           "copied  $file"
       }
   }
   ```

   Not copied: `.venv` (built again), `data/logs/`, `data/tmp/` and the other logs.

   What lives outside the app folder stays where it is on the same PC: the session folders and their `_library`, the tunnel credentials under `%USERPROFILE%\.cloudflared\`, and the usage log under `C:\sqlite\facilitation-suite\`. Check two cases:

   - A session folder or `session_root` **inside the old folder:** move it out, and fix its path in `sessions.local.yaml` or `config/config.json`.
   - A usage log still at `data/usage.sqlite3` in the old folder: after step 4, copy it to `C:\sqlite\facilitation-suite\`, only if no `usage.sqlite3` is there yet.

3. **Top them up.** In the new folder run `.\setup.bat -WhatIf`, read what it would change, then `.\setup.bat`. The config gains only the keys it lacks; the values you copied stay as they were.

4. **Stop the old copy.** Right-click its tray icon → **Quit**. Quit leaves a server the tray did not start itself, so check that nothing is listening; this should print nothing:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8449,8451,8456 -ErrorAction SilentlyContinue
   ```

   If it prints a line, `OwningProcess` is the id of the process to end. Delete the old copy's shortcut in `shell:startup`. If you used the old copy after step 2, run the copy block again now.

5. **Start the new one.** `.\tray.bat` in the new folder, and a new `shell:startup` shortcut to it if it should start at log on. Only one tray runs per PC, so a new one started while the old one is up exits at once.

6. **Verify it.** `curl.exe -s http://127.0.0.1:8456/api/version` shows the `git_sha` that `git rev-parse --short HEAD` prints in the new folder. Open `http://127.0.0.1:8456/`: your sessions are listed and open, and Settings shows your values. Try what you use: the phone remote, the quiz link, music. If anything is wrong, quit the new tray and start the old one again: its files are untouched.

7. **Retire the old folder, only after you confirm.** Nothing in this playbook or in `setup.bat` deletes the old copy. When you are satisfied, rename it first (for example `<OLD FOLDER>.retired`) and run on the new copy for a while; delete it yourself once you are sure. An agent working through this playbook asks you before it deletes anything.

## Uninstall

1. Delete the shortcut to `tray.bat` in `shell:startup`.
2. Quit the tray from its icon, and check that nothing is listening (step 4 above).
3. What the app uses outside its folder, each only if you no longer want it:
   - the usage log, `C:\sqlite\facilitation-suite\`;
   - the quiz tunnel: `cloudflared tunnel delete facilitation-quiz`, its DNS record in Cloudflare and its credentials file under `%USERPROFILE%\.cloudflared\`, only when no other PC serves it;
   - the Spotify app's access, removed from your Spotify account page.
4. Delete the folder. Keep a copy of the files listed under "Migrate" first if you may want them back. Your session folders are not inside it and stay.
