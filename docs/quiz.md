# Quiz

A Kahoot-style quiz is planned as items (players play on their phones — see [Quiz player](quiz-player.md) — and the stage shows the lobby, questions, reveals, leaderboards and podium — see [On the stage](#on-the-stage)). Three activity types, all in the Plan tab's activity **Type → More…** menu:

- **Quiz lobby** starts a game; its **Quiz name** is the title on the stage. The game is the quiz questions after it, in plan order, up to the next quiz podium.
- **Quiz question**: the question, **Answer 1–4** (two to four), **Correct answer(s)** (the answer numbers, as Kahoot writes them: `2`, or `1,3` for several), **Time limit** (5, 10, 20, 30, 60, 90, 120 or 240 s; 20 by default) and **Points** (standard, double or none).
- **Quiz podium** ends the game.

**Import Kahoot** (Plan tab toolbar) reads Kahoot's own spreadsheet import template (`KahootQuizTemplate.xlsx`, header row with *Question*, *Answer 1–4*, *Time limit (sec)*, *Correct answer(s)*; the questions end at the first empty question cell): pick the `.xlsx` on this PC, name the quiz (the file's name by default), and a new section at the end of the plan gets the lobby, one question per row and the podium — saved to `session.yaml` at once, with a planned-minutes estimate (every time limit plus 30 s each). A row that breaks the format (no correct answer, a correct answer pointing at an empty answer, fewer than two answers, a time limit Kahoot does not offer) refuses the whole file with a message naming the row, and nothing is saved. Kahoot's report `.xlsx` is not read. In `session.yaml`:

```yaml
- kind: activity
  type: quiz
  question: Which planet is known as the red planet?
  options: {answer_1: Venus, answer_2: Mars, answer_3: Jupiter, answer_4: Saturn,
            correct: "2", time_limit: 20, points: standard}
```

**The game** (`src/quiz/`) runs on the server; phones and the stage only render it. The first time the stage reaches a quiz lobby (or one of its questions) a game opens. Coming back to the lobby later resumes the same game, and `quiz_new_game` on the lobby starts a fresh one to play it again.

- **Phases.** Entering a question opens it with a deadline of its time limit. **Next** then steps *question → reveal* (the answers lock; the answer distribution and the correct answer show) *→ leaderboard*, and only from the leaderboard moves on to the next item. The **last question of a game that ends on a podium** skips its leaderboard, as Kahoot does: Next on its reveal goes straight to the podium (nothing shown yet), so the stage never shows the final top 5 before the podium reveals them, and **Previous** from there comes back to that reveal. A quiz with no podium after it keeps its final leaderboard.
- **Time up** reveals by itself, and so does `quiz_lock` or **Space** while the question is open (lock answers now). So does every active (non-kicked) player answering, about 1 s after the last one does — the lobby's **End questions when everyone has answered** switch (on by default; off leaves the question open for its full time limit). It never fires with no players, and a late answer after the reveal still gets *too late*, exactly as after time up. Once revealed, Space is back to the item's timer, if it has one.
- **Previous** goes to the previous item (on the podium, see below). A question left while it is still open is locked. Coming back to it shows its reveal again, never a second chance to answer.
- **Podium.** Each **Next** reveals one place: 3rd, then 2nd, then 1st (with fewer than three players, only the places that exist), and the Next after 1st moves on to the following item. **Previous** hides the last place revealed, and with none shown goes to the previous item. The step is written to `live/quiz.jsonl`, so a restart keeps it; coming back to the podium from the item after it shows it as it was left, and reaching it from the game's last reveal starts it over.
- **Other items.** On every other item, including the lobby, next and previous work as before.
- **Answers** come from phones (see [Quiz player](quiz-player.md)) and from the Zoom chat (below). A quiz question never opens a capture window, and the Space key does not start one.
- **Scoring** follows Kahoot's published rule: a correct answer scores `round(1000 × (1 − (response time / time limit) / 2))`, full points inside the first half second, ×2 for **double**, 0 for **none**. A wrong or missing answer scores 0.
- **Response time** runs from when the buttons appeared on that phone, clamped to how long the server has been asking.
- **Streaks.** Answer streaks are counted and shown but score nothing, as in Kahoot today.
- **Ties** go to the smaller total response time over correct answers, then to who joined first.

Every join, answer, kick and phase change is appended to `live/quiz.jsonl`, and phase changes also go to `live/events.jsonl`. A restarted server replays `quiz.jsonl` and resumes every game with the same players, answers and scores. Scores are always derived, never stored. A question whose time ran out while the server was down is revealed at once. Stream Deck: `quiz_lock`.

**Running it.** On a quiz item the presenter's side card becomes **Quiz**: the phase (lobby, question *n* of *N* with its time left, answers locked, leaderboard, podium), players joined and answered, the join **PIN** and public link with a **Copy link** button (or *not configured* while the player page or `quiz.public_url` is not set up), the public link's **Reachability** chip with a **Check** button (see [Running a quiz](running-a-quiz.md)), and the controls:

- **Next phase** (→) is the same `next` as everywhere: on the lobby it opens the first question, then *reveal → leaderboard → next question*, from the last reveal straight on to the podium (the button says *On to the podium*; with no podium, the last question keeps its leaderboard), and there *Show 3rd → Show 2nd → Show 1st* (the phase line says which place shows). There is no separate "next phase" action, because `next` already is one.
- **Lock answers** (Space) reveals the question now (`quiz_lock`).
- **New game**, on the lobby only and after a confirmation, plays the quiz again with no players (`quiz_new_game`); the game so far is kept.
- The **player list** marks chat players and has a remove button per player (after a confirmation; `quiz_kick/<id>`).

The phone remote shows the same essentials on a quiz item — the phase, players, answers, PIN and time left, with **Lock answers**; its **Next** steps the phases.

**Chat fallback.** If the public link fails mid-session, say "type your answer in the chat": while a question is open, a Zoom chat message that is exactly `A`–`D`, `a`–`d` or `1`–`4` (optionally followed by punctuation — `B`, `b.`, `3!`) is that sender's answer, scored by the same engine (`src/quiz/chat.py`). The sender joins as a chat player on their first answer, with their Zoom name as the nickname (a phone player with the same name keeps it; the chat player gets `(2)`, so both stay distinct on the leaderboard). The first answer wins, as on a phone; a letter the question has no answer for (`D` on a three-answer question) is ignored; the response time is from the question opening to when the chat reader saw the message; messages outside an open question are ignored, and so are your own — unless **Count my own chat answers (rehearsal)** is on. The quiz lobby's **Answers typed in the chat count too** switch (on by default) turns it off for that game; while it is on, the stage says *Answer on your phone or type A–D in the chat* under an open question. Known trade-off: chat answers are visible to everyone in the chat, so people can copy.

**Rehearse alone:** on a quiz question, the presenter's **Simulate answers** makes twelve simulated people type a random letter each through the chat simulator — the whole game, lobby to podium, runs without anyone else.

## On the stage

The stage draws a quiz with `app/activities/quiz/stage.js`, one renderer for the three types, in the session theme and fonts — never the fleet design:

- **Lobby:** the join QR code, the public link and the PIN (`123 456`), the player count and the names popping in as they join — as many as fit in full rows, then a *+N more* chip. While phones cannot join, it says so instead of showing a QR that leads nowhere: *Joining by phone is not set up* (`quiz.public_url` empty) or *not available right now* (the player listener is down).
- **Question:** *Question n of N*, the answers on tiles in the phones' colours **and** shapes with their letters A–D (so chat players know what to type), a countdown ring and seconds from the server's deadline (corrected for this screen's clock, within a tenth of a second; its font steps down for a 3-digit limit, Kahoot allows up to 240 s), and *x / y answered*. An answer too long for its tile (Kahoot allows 75 characters) steps the answers' size down until every line shows, inside the tile's padding and without splitting a word; if they still do not fit at their smallest (a long question beside a camera strip), the question steps down too. Nothing on the stage marks the correct answer before the reveal: the renderer reads it only from the game's snapshot, which carries it only once the question has closed — never from the item's options.
- **Reveal:** a bar per answer with its count, the correct one(s) marked with a check and a *Correct* tab on the tile's frame (never by colour alone), the other tiles dimmed. Every bar keeps a minimum height so its shape icon always shows whole, even at 0 votes. The bars move up into the band a corner camera keeps free under the title (beside the camera, never under it), so the tiles keep room for three lines of a 75-character answer.
- **Leaderboard:** the top 5, each row sliding from where it stood before the question to its new rank, with the points just won. Under a corner camera the board narrows beside the camera, never under it.
- **The camera:** every phase follows the item's OBS profile, and re-lays itself out within a second when the profile changes — live or in the Plan tab's preview, with no reload. *Screen only* uses the full width; beside a *Camera strip* everything fits the left part; under a *Camera PiP* the title flows around the corner box (its lines beside the camera stop short of it, those below take the full width) and the question's status row and the reveal's bars rise into the band beside it, the tiles below it.
- **Podium:** one place per **Next** — 3rd, then 2nd, then 1st — each rising as it is revealed. Every place keeps its spot from the start, so nothing shifts when the next one appears.

The answer colours match the phones (a session's `theme.css` can move them with `--st-quiz-1` … `--st-quiz-4`, and the podium's with `--st-podium-1` … `--st-podium-3`). With **reduced motion** set in the OS nothing pops, rises or slides. The Plan tab's and the presenter's previews, with no game running, draw the question as it opens and sample players (`samples` in the lobby's and podium's `editor.json`). Quiz items are not captures, so a capture freeze (and its PNG) does not apply to them; the session PDF draws the quiz from `live/quiz.jsonl` (see [Results](results.md)).

## Sound cues

Optional, played on this PC through the app's music, like item music — never in a browser. Put any of these in the session's `audio/` folder, as `.mp3`, `.wav`, `.ogg` or `.flac`, and the quiz plays them at its phase changes; without them it stays silent.

| File | Plays |
|---|---|
| `audio/quiz-lobby.*` | on the lobby, looping until the first question |
| `audio/quiz-countdown.*` | once, when a question opens |
| `audio/quiz-reveal.*` | once, at the reveal |
| `audio/quiz-podium.*` | once, when the podium reveals 1st place |

A cue still sounding fades out at the next phase without a cue of its own (the leaderboard) and when the stage leaves the quiz. **Item and presenter music always win:** while an item's music or music you played by hand is playing or paused, the cues are skipped and that music plays on untouched; pressing play, a timer's music or **Stop** replaces or ends a cue like any track. The presenter's Music card shows a cue as the track playing (pause, stop and volume work on it). Kahoot's own music is not reusable: bring royalty-free tracks or your own.
