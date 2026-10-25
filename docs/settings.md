# Settings

The gear in any tab's header opens **Settings**: what is common to every presentation, in five sections — each with its own link, `/#settings/<section>` (the presenter's music chip opens `/#settings/music`).

| Section | What | Global or per session |
|---|---|---|
| **Appearance** | light / dark / system for the app, the presenter and the phone remote · text size | global (every device) · text size per device |
| **Stage defaults** | the stage theme and lettering (title font, text font, each kind of text) new sessions start from · the **stage library** of fonts and themes | defaults only: a new session **copies** them into its `session.yaml`, then owns them |
| **Music** | the Spotify account (state, device, last check, **Connect / Reconnect Spotify**) · the fades a newly switched-on music item starts with | global (the login is in `.env`, the fades in `config/config.json`) |
| **Live tools** | OBS profiles and connection · the chat reader (Zoom or Teams) · what a click on the stage does · the Stream Deck buttons · the phone remote | global |
| **About** | the build and the credits | — |

**Per session** stays in the session's own plan: its stage theme and lettering (Sessions tab → **Session settings** → *Stage look*), each item's own font exceptions and music (Plan tab). Changing a default never touches an existing session. The session's Stage look says **Using the default** or **Overridden**, marks each title font, size and capitals that differs from the default with its own **Reset to global**, and **Reset all to defaults** copies the current defaults in after a confirm (the session's theme and lettering; items keep their own exceptions). The stage always follows the session's own look.

**The stage library** holds your own font files and stage themes, outside the repo: `<session_root>\_library\fonts\` and `<session_root>\_library\themes\` (next to your session folders, so on OneDrive by default; Settings shows the path). **Add font…** / **Add stage theme…** copy a file there (the same file again is reused; a different one with the same name becomes `<name>-2`); a font picked for the default lettering is copied in too. Library fonts appear in every title-font list. A library theme is a `.css` file whose rules are scoped under `.stage-canvas`, like `themes/default.css`; a session names it `theme: library/<name>` and its CSS reaches the stage inside the session's `theme.css`.
