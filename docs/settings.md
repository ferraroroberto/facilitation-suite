# Settings

The gear in any tab's header opens **Settings**, a full page over the current tab (close it, or press Esc, to return): what is common to every presentation, in six sections — each with its own link, `/#settings/<section>` (the presenter's music chip opens `/#settings/music`).

Each section is inset groups of one-line rows, each under its sentence-case label (the fleet `settings-group` component): a row holds one trailing item — a value that opens a sheet, a picker, a switch or a number field. Switches, pickers, numbers and the light/dark and text-size choices save as you change them; what needs room (Spotify, the lettering editor, the stage library, the phone remote, the Stream Deck URLs, the credits) opens in a sheet that fits the screen, with Done pinned at its foot.

| Section | What | Global or per session |
|---|---|---|
| **Appearance** | light / dark / system for the app, the presenter and the phone remote · text size | global (every device) · text size per device |
| **Available features** | one switch per feature block this PC runs (see below) | this PC (`config/config.json` `features`) |
| **Stage defaults** | the stage theme and lettering (title font, text font, each kind of text) new sessions start from · the **stage library** of fonts and themes | defaults only: a new session **copies** them into its `session.yaml`, then owns them |
| **Music** | the Spotify account (state, device, last check, **Connect / Reconnect Spotify**) · the fades a newly switched-on music item starts with | global (the login is in `.env`, the fades in `config/config.json`) |
| **Live tools** | OBS profiles and connection · the chat reader (Zoom, Teams or Meet) · the Copilot overlay (screen, position, mode, look — see [Live copilot](copilot.md#the-overlay)) · what a click on the stage does · the Stream Deck buttons · the phone remote | global |
| **About** | the build and the credits | — |

## Available features

One checkout serves several PCs, and each switches off what it cannot or will not run, without editing code or a tracked file. Every block is on by default; the choice is saved for this PC in `config/config.json` under `features` (never committed).

| Block | Switched off |
|---|---|
| **Chat reader** | the reader never starts (a running one stops at once); the presenter loses the chat source switch and the Start reader button, the readiness list its reader check, Settings its Chat reader group; the reader routes answer 404 |
| **OBS scene switching** | the OBS connection closes and no item switches a scene; the presenter's OBS chip, the OBS group and the readiness check go; the `obs_profile` action is refused |
| **Live copilot** | Copilot and its overlay window stop; the presenter's Copilot card and chip, the phone's hints, the overlay and recordings groups go; `/api/copilot` answers 404 and `copilot_toggle` is refused |
| **Music** | music stops; item music, the quiz sounds and every music action are skipped; the presenter's Music card and chip, the phone's music card, the Music section and its readiness checks go; the Spotify routes answer 404 |
| **Public quiz link** | the quiz player's public listener stops and the public-link check stops; the tray starts no Cloudflare tunnel (after a tray restart, which the toast says); the quiz still runs on chat answers |
| **Phone remote** | `/remote` answers 404, the pairing link goes, and every request from another device is refused, a paired phone included |

A block whose dependency is missing on this PC keeps its switch and says why under its name (the reader needs Windows and comtypes, OBS needs obsws-python, the copilot and music need miniaudio, the public quiz link needs `webapp/cloudflared.yml`, cloudflared on PATH and a `quiz.public_port`, the phone remote needs the tailnet certificate). An older `config.json` that still carries `obs.enabled` or `reader.enabled` and no `features` value for that block reads it as that block's switch, so it keeps meaning what it meant without being rewritten. That is why `config/config.sample.json` lists neither `features.obs` nor `features.chat_reader`: `setup.bat` adds the keys a config lacks, and an added value would override the older switch.

**Per session** stays in the session's own plan: its stage theme and lettering (Sessions tab → **Session settings** → *Stage look*), each item's own font exceptions and music (Plan tab). Changing a default never touches an existing session. The session's Stage look says **Using the default** or **Overridden**, marks each title font, size and capitals that differs from the default with its own **Reset to global**, and **Reset all to defaults** copies the current defaults in after a confirm (the session's theme and lettering; items keep their own exceptions). The stage always follows the session's own look.

**The stage library** holds your own font files and stage themes, outside the repo: `<session_root>\_library\fonts\` and `<session_root>\_library\themes\` (next to your session folders, so on OneDrive by default; Settings shows the path). **Add font…** / **Add colour set…** copy a file there (the same file again is reused; a different one with the same name becomes `<name>-2`); a font picked for the default lettering is copied in too. Library fonts appear in every title-font list. A library colour set (a stage theme) is a `.css` file whose rules are scoped under `.stage-canvas`, like `assets/themes/default.css`; a session names it `theme: library/<name>` and its CSS reaches the stage inside the session's `theme.css`.
