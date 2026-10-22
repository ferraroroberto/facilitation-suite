# Phone remote

`/remote` on the phone: what is on stage (a live preview), what comes next, the session clock and how far off the plan it is, and big buttons for **Next / Previous**, **Start / Stop capture** (on an activity), the item **timer** (start/pause, +1 min) and **Blackout** — the same intents as the keyboard — plus the **music** (play/pause, stop, volume) when the session has any, and on a quiz item its phase, players, answers, PIN, time left and **Lock answers**. **Chat** shows the Zoom chat (tap a message to hide it from the activity, tap again to count it back); **Groups** shows the breakout rooms of each round to read out.

1. Set up HTTPS once (see [HTTPS](operations.md#https)).
2. **Settings → Phone remote → Make the phone link**, **Copy the link**, send it to yourself and open it on the phone (on the tailnet). Opening it pairs that phone: the server sets a 30-day cookie and the token leaves the address bar.
3. **New link** unpairs every phone that had the old one; **Turn off** locks every other device out.

With `host` set to `127.0.0.1` (config `host`, see [Configuration](configuration.md)), the card shows **unavailable** instead of a link: a phone can't reach a listener bound to loopback only, however good the cert. Set `host` to `0.0.0.0` and restart the tray.

Every other device needs that token for everything (the app, the API, the live connection) — as a pairing cookie, an `Authorization: Bearer` header or `?token=`; without it, `401`. The pages and API stay open to this PC itself: loopback, or this PC reaching itself through its tailnet name (the connection comes from the same address it arrives on). The token lives in `config/config.json` → `remote.token` (gitignored), is never logged (request lines are redacted), and the native file picker, the chat reader's endpoints and the remote's own Settings stay PC-only even with it.
