# Running a quiz

End to end, from a Kahoot spreadsheet to the podium. The details of each part are in [Quiz](quiz.md) and [Quiz player](quiz-player.md).

1. **Import.** Plan tab → **Import Kahoot** → pick the `.xlsx` (Kahoot's spreadsheet template). A new section holds the lobby, one item per question and the podium; edit any question in the Plan tab.
2. **Sound (optional).** Put `quiz-lobby.*`, `quiz-countdown.*`, `quiz-reveal.*` and `quiz-podium.*` (`.mp3`, `.wav`, `.ogg` or `.flac`) in the session's `audio/` folder; the Readiness list does not check them, a missing one is simply silent.
3. **Tunnel up.** With the one-time setup done ([Quiz player](quiz-player.md)), the tray runs cloudflared and `quiz.public_url` = `https://quiz.robertoferraro.net`; the Reachability chip says *reachable*. Nothing to switch on per session: between sessions the tunnel serves the player page with no game (*no game* on the phone).
4. **The player link.** On the lobby the stage shows the QR code, the link and the PIN; the presenter's Quiz card has **Copy link** for the chat. Players open it on their phone (mobile data works; they are not on the tailnet).
5. **Rehearse.**
   - *Alone, through the chat:* on a quiz question, **Simulate answers** (the presenter) makes twelve simulated people answer in the chat; the game runs lobby to podium.
   - *With bots, through the player API:* `& .\.venv\Scripts\python.exe scripts\quiz_bots.py` boots a **disposable** instance with a synthetic session (never the live app or a real session folder), plays a 10-question game with 60 bots (joins, answers with retries, random reloads, WebSocket ↔ polling) as the host, and checks against `live/quiz.jsonl` that every bot joined once and every answer was recorded, acked and scored exactly once. `--restart-mid-question` also kills the server mid-question, restarts it and checks the session came back live by itself (no *Go live*) with the game and every score as they were. Through the public edge: publish the disposable instance on a temporary hostname of the tunnel (e.g. `quiz-bots.<domain>`: a DNS route plus a rule in `webapp/cloudflared.yml`, then `tray.bat --restart` — keep one connector, since Cloudflare spreads a tunnel's requests over all its cloudflared processes) and pass `--player-port` / `--player-base` / `--public-dns` (the script's docstring has the commands); the live player (`quiz.public_url`'s host and the tunnel config's hostnames, at the root) is refused. Remove the rule afterwards; `cloudflared` cannot delete the DNS record, so remove it in the Cloudflare dashboard or leave it — the live config's catch-all answers it with a 404.
6. **Chat fallback.** If phones cannot join or the link dies mid-game, say "type A, B, C or D in the chat": while the lobby's *Answers typed in the chat count too* is on (the default), chat answers score through the same engine, and the game carries on.

**Reachability check.** For a session with quiz items the Sessions tab's *Ready for the live session* list has **Quiz public URL reachable**, and the presenter's Quiz card has the same state on its **Reachability** chip; both have a **Check** button. The check (`src/quiz/reach.py`) pings `/play/api/ping` on the player listener on this PC, asks cloudflared's `/ready` on `127.0.0.1:8452` when the host is one `webapp/cloudflared.yml` publishes, then resolves the public host through **public DNS** (1.1.1.1, then 8.8.8.8, asked directly — never this PC's own resolver, which can answer differently, e.g. MagicDNS for a ts.net name) and pings the same path through the public edge, as a phone on mobile data would. It runs in a worker thread when a quiz session goes live, every 2 minutes while it is live, and on demand. Each state is distinct:

| State | Meaning |
|---|---|
| *reachable* | the ping answered through the public edge |
| *listener down* | the player listener on `127.0.0.1:8451` does not answer (busy port? see the log) |
| *tunnel down* | the host is the tunnel's and cloudflared is not running, or has no connection to Cloudflare (`tray.bat --restart`; `data/logs/cloudflared.log`) |
| *public URL unreachable* | public DNS does not know the host (no DNS record, or not propagated yet), or the edge does not answer the ping (e.g. the 502 Cloudflare gives when no cloudflared serves the host — webapp/cloudflared.yml missing on this PC? — or the Funnel relay's) |
| *not configured* | `quiz.public_url` is empty or not `https://`, or `quiz.public_port` is 0 |
| *unknown* | not checked yet, the last check is over 5 minutes old, public DNS did not answer (is this PC online?), or the check itself failed — never counted as ready |

## Measured limits: one public IP

All from this PC's one public IP — the office-NAT case.

- *Cloudflare tunnel* (measured 2026-09-29): **no per-IP cap found up to 200 connections**: 200 WebSockets opened at once to one edge address all opened, stayed open 15 s and answered a ping, and 200 concurrent HTTP pings all answered. `scripts/quiz_bots.py` with 60 bots × 10 questions through a temporary tunnel hostname: 60 of 60 joined in 8 s, 600 answers acked and scored exactly once (join p50 80 ms, answer ack p50 71 ms / p99 110 ms); with `--restart-mid-question` the same, the game back as it was 7.8 s after the kill (phones saw the tunnel's 502 meanwhile and retried). More than 200 was not tried.
- *Tailscale Funnel* (the fallback; measured 2026-09-28): each Funnel relay address accepted **20 concurrent connections** from one IP; the 21st and later were reset or timed out until one closed. A phone holds one or two (the socket, plus the page's HTTP connection while it polls or answers), so a room on one NAT seats only about 10–20 players per relay address: 60 bots from one IP got 44 joins in 7 minutes. Phones on mobile data each have their own address.

**Before the session:**

- [ ] The tunnel is up (the tray's **Status** says *quiz tunnel running*) and `quiz.public_url` set.
- [ ] *Quiz public URL reachable* is **reachable** (press **Check**); not *unknown*.
- [ ] Open the join link on a phone **on mobile data**, join the lobby, answer one question — then **Remove** that test player (or **New game** on the lobby).
- [ ] Sound cues in `audio/` play (optional).
- [ ] The chat reader is tested (the chat fallback depends on it) and the lobby's chat switch is as you want it.
- [ ] The chat fallback is ready anyway (a network that blocks the quiz host, or the tunnel down mid-game).
- [ ] A rehearsal with `scripts/quiz_bots.py` passed on this build.
