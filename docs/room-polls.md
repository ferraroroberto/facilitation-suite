# Room Polls demo and the owner's phone test

Room Polls lets people in the room answer on their phones (QR code or link, then the PIN) while people online answer in the meeting chat, both on the same poll slide. The demo session puts every piece of it in one plan, so it can be tried end to end with a real phone before a workshop.

## The demo session

`tests/fixtures/demo-room-polls/session.yaml` is a synthetic plan made only of activities, so it needs no deck:

| Section | Items |
|---|---|
| Join | a Join slide (QR code, link, PIN) |
| Warm-up quiz | lobby, two questions, podium |
| Polls | word cloud, choice, multi choice, scale 0–10, stars, NPS, number (minutes), free text (feed) |
| Feedback | a private form with five questions: NPS, stars, multiple choice, scale, text |

`python -m scripts.room_polls_demo` creates it on a running app, through that app's own API, in the session root from Settings (workshop folder `demo`, session folder `room-polls-demo`). Run again, it finds the same session and puts the plan back as the file has it; live data already in its folder stays. It prints the session id and the presenter URL, then checks the public join link (`POST /api/quiz/reach`) and prints `REACH=<state>`, exiting 2 unless the state is `ok`. `--base` points it at another app (default `https://127.0.0.1:8449`), `--no-reach` skips the check.

## The owner test script

On the PC, with the tray running:

1. `.\.venv\Scripts\python.exe -m scripts.room_polls_demo` — expect `REACH=ok`. Anything else names why phones cannot join yet ([Running a quiz](running-a-quiz.md) explains each state); fix that first.
2. Open the presenter URL it printed, and `/stage` on the display. Go live. The Join slide shows the QR code, the link and the PIN.
3. Scan the QR code with the phone on mobile data (not the office Wi-Fi), skip the name, and join. The Join slide's count goes up.
4. Next to the quiz lobby: the phone asks for a nickname (the quiz needs one). Answer both questions, then follow the reveal, the leaderboard and the podium.
5. For each poll: press Space to start the capture, answer on the phone, and type one answer in the meeting chat. The stage counts both channels (💬 · 📱). Change the phone's answer once: the stage replaces it. Press Space to stop; the capture freezes.
6. On the form: the phone shows *Open the form*. Send it twice (repeat submissions are allowed), once with a name, once without. The presenter's panel adds the responses up; the stage shows no answers.
7. Check the results: the Results tab lists each poll's capture, and `live/captures/demo-form.csv` in the session folder has one row per form response (also from the presenter's form panel).

## The automated walk-through

`tests/e2e/test_room_polls_demo.py` drives the same session in a disposable instance: two phone contexts join the room, play the quiz, answer every poll beside fake chat lines (each result must render on the stage and freeze into a capture JSON and PNG), and send the form twice, whose CSV must have a row each. It screenshots every step into a proof page, `data/proof/room-polls-demo/index.html` (`FS_PROOF_DIR` moves it).

It is not part of the gate, which keeps the e2e suite under 15 nodes; `tests/e2e/conftest.py` leaves it out of a directory run. Run it by path after a change to the room, the polls or the forms:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_room_polls_demo.py
```
