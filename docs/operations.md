# Running and operating the app

## Appearance

**Light or dark is one setting for all your screens**: the moon/sun button in the app's header, the presenter or the phone remote — or **Settings → Appearance** (System / Light / Dark) — switches the app, the presenter and the phone remote on every open device at once, within a second and without a reload. *System* follows each device's own OS setting. It is kept in `config/config.json` (`appearance`) and pushed with the live state; each browser also caches it (`facilitation-suite.theme`) so a page opens in the right theme with no flash. **Settings → Text size** (Small / Default / Large) scales the app's text on this device only (`facilitation-suite.textsize`). The stage keeps the session's look and sizes, and the players' `/play` page its own light/dark button.

## HTTPS

For the phone remote: `& .venv\Scripts\python.exe scripts\gen_tailscale_cert.py` writes a Tailscale certificate (a real Let's Encrypt leaf for this PC's tailnet name) to `webapp/certificates/`; after `tray.bat --restart` the server speaks HTTPS only, on the tailnet name. The leaf lasts ~90 days and renews itself at every start (`--check`), so there is no date to remember.

## Logs

Logs live in `data/logs/` (under `FS_DATA_DIR` when it is set). Each file has exactly one writer, because Windows cannot rotate a file another process holds open; each rotates at 1 MB and keeps 3 backups (`.log.1`–`.log.3`):

| File | Written by |
|---|---|
| `facilitation-suite.log` | the webapp (the server, including `✅ facilitation-suite up — build <sha>`), plus the lines of the freeze-PNG and session-PDF helpers it spawns — they log to stderr and the server relays them |
| `tray.log` | the tray: starting, adopting and restarting the webapp |
| `chat-reader.log` | the chat reader (Zoom or Teams) |
| `cloudflared.log` | cloudflared, the quiz player's tunnel (its own writer; the tray moves it to `.log.1` past 5 MB when it starts cloudflared) |
| `webapp/watchdog.log` (repo root) | the tray's watchdog breadcrumbs |

An **outside** process (a stray old server, an editor, a `tail`) can still hold a log file open even with one writer per file. Rotation then keeps appending to the current file instead of dropping records, logs one `⚠️ log rotation blocked` line and retries once the outside handle closes.
