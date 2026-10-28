# Configuration

`config/config.json` (gitignored; `config/config.sample.json` documents every key):

| Key | Meaning |
|---|---|
| `host` | bind address for the tray's and `webapp.bat`'s server: `0.0.0.0` (default) listens on every interface, which the phone remote and the Stream Deck over the tailnet need; `127.0.0.1` keeps the app on this PC only (off the LAN and the tailnet — the tray's **Open** then uses the loopback URL). Restart the tray after changing it |
| `port` | server port (8449) |
| `local_port` | the local listener: the same app in plain HTTP on `127.0.0.1` only (8456; `0` = off), what the tray's **Open** uses on this PC while `port` serves HTTPS to other devices. A busy port is logged and the tray opens the HTTPS URL instead. Restart the tray after changing it (see [HTTPS](operations.md#https)) |
| `session_root` | default parent folder for new sessions (`<root>\<workshop>\<session>\`) |
| `appearance` | `system` (default), `light` or `dark` for the app, the presenter and the phone remote — set from any sun/moon button or Settings → Appearance |
| `obs` | obs-websocket host / port / password (the password stays in this file only), `enabled` = scene switching on/off |
| `profiles` | the three OBS profiles: each one's OBS `scene` and the camera `zone` the stage keeps empty (`[left, top, right, bottom]` as fractions, `null` = no camera) |
| `reader` | the chat reader: `source` (`zoom`, the default, `teams` or `meet`), poll interval, the Zoom chat window's class and title, `teams_title` (what a Teams window's title carries), and `meet_title` and `meet_panel` (the word a Meet window's title names, and the chat panel's title) |
| `remote` | `token`: the phone remote's bearer token (a secret — made and replaced from Settings; empty = only this PC gets in) |
| `defaults` | what new sessions start from (Settings → Stage defaults and Music): `stage.theme` (a repo theme or `library/<name>`), `stage.font` (a `session.yaml` `font` block; absent = the theme's lettering), `music.fade_in_s` / `fade_out_s` (0–60, default 2) — a bad value falls back, logged |
| `stage_click` | what a click on the stage window does (Settings → Live tools → Clicking the stage; the open stage follows a change at once): `advance` goes to the next item (default `false`), `restore_camera` re-applies the item's OBS profile when `advance` is off (default `true`) |
| `live` | `resume_on_start`: after a restart, take the session that was live when the server stopped live again (default `true`; a closed session never comes back) |
| `quiz` | `public_port`: the quiz player listener on `127.0.0.1` (8451; `0` = off) · `public_url`: its public address (`https://quiz.robertoferraro.net`, the Cloudflare tunnel), empty until published (see [Quiz player](quiz-player.md)). Restart the tray after changing it |

**`.env`** (gitignored, repo root) holds secrets only: `SPOTIFY_CLIENT_ID`, `SPOTIFY_REFRESH_TOKEN` and the optional `SPOTIFY_DEVICE_NAME` (see [Spotify setup](music.md#spotify-setup-once)); `FS_ENV_PATH` points elsewhere. `.env.example` (committed) documents the same keys with placeholder values — copy it to `.env` and fill in real values, never commit `.env` itself.

The **ledger** `sessions.local.yaml` (gitignored; example in `sessions.example.yaml`) lists session names and folders only. Each session lives in its own folder with its own `session.yaml`.
