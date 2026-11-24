# Feedback forms

A feedback form is a private form for end-of-session feedback and NPS, like Microsoft Forms or Google Forms. Unlike the live polls ([Activities](activities.md)), **its answers are never shown in the room**: only the presenter's backstage panel and a CSV next to the captures see them.

## Building a form

A form is a Plan item: add an activity and pick the type **Feedback form**. The item's title is the form's title (empty: *Your feedback*, or *Tu opinión* in a Spanish session). The editor's form builder holds the rest, saved with the plan in `session.yaml` (`options.intro`, `options.show_count`, `options.questions`):

- **Intro**: the text the phone shows under the title.
- **On the stage**: *Show how many responses came in*, off by default. The stage never shows an answer either way.
- **Questions**, in order, each with a type, its text, *Required* and the type's own fields:

| Type | The phone shows | Stored as |
|---|---|---|
| Free text | a text box (1,000 characters, line breaks kept) | the text |
| Number | a number box with the unit, in the optional range | the number |
| Scale 0–10 | a button per number, the words under 0 and 10 | the number |
| Stars 1–5 | five star buttons | the number |
| NPS 0–10 | a button per number, the words under 0 and 10 | the number; scored like the NPS poll |
| Choice | a lettered button per choice, several when *Allow more than one choice* is on | the letters, `A,C` |

**Copy from another session** lists every form in the ledger's sessions and copies one's intro and questions into this item, so one form serves many sessions.

## Running it

The form **opens by itself the first time its item comes on stage** and stays open when the stage moves on, so people can finish it while the session goes on. The presenter's item card (titled *Feedback form*) has **Close form**, **Reopen form**, **Copy link** (the public link to paste in the meeting chat) and **CSV**. Closing the session closes every form.

Two ways in, one page, `<quiz.public_url>/play/form?t=<token>` on the public quiz player:

- **in the room**: the stage shows *Scan to give your feedback* with the form's QR code and link, large, plus the response count when the form shows it. A phone already in the session room gets *A feedback form is open* with **Open the form**. The QR is shown only while the public link is reachable (the same check as the join badge); otherwise a note sends people to the link in the chat.
- **online**: the presenter pastes the link in the meeting chat; it opens the same page in any browser, with no PIN and no room join.

The phone page is one scroll: the title, the intro, every question, an optional name, then **Send**. A required question left empty, or an answer the question cannot take, is outlined with the reason and nothing is stored. After sending, *Thank you!* and **Send another response**: each submission is its own row. A resend of the same submission (a dropped connection) is stored once. The token stays the same for the session, so a link pasted in the chat works again after a reopen; while the form or the session room is closed, the link answers *This form is closed* (HTTP 410).

## Results

The presenter's panel reads `GET /api/forms/<item>/results` every few seconds while it is on screen: the response count, and per question its answer count, the NPS score with promoters, passives and detractors, star and scale averages, a number's average and range, each choice's tally, and the free-text answers, newest first, with names where given.

Every submission rewrites `live/captures/<item>.csv` in the session folder: one row per submission (*Submitted*, *Name*, then one column per question), UTF-8 with a BOM so Excel opens it as is. **CSV** on the panel downloads the same file. The submissions themselves are appended to `live/forms.jsonl` (open, close and submit records), replayed on activate, so the results survive a restart.

## Privacy

- No answer is ever put in the live snapshot that the stage, the presenter and every `/ws` client receive: it carries the form's open state, link and, when the form shows it, its count (`state.join` on a form item, `state.forms_open`). The backstage results come from a presenter-only HTTP route behind `RemoteAuth`.
- A phone gets its own acknowledgement and nothing else: no other answer, no count.
- Submissions are rate-limited per address (200, then 2 a second, for a room behind one NAT) and per phone (6, then one every 10 s, keyed by an id the page keeps in the phone's storage).
- `forms.jsonl` and the CSV live in the session folder, outside the repo, like every other capture.

## API

Main app, behind `RemoteAuth`:

    GET  /api/forms/<item>/results   the backstage view
    POST /api/forms/<item>/open      open (or reopen) the form
    POST /api/forms/<item>/close     close it
    GET  /api/forms/<item>/csv       the responses as CSV
    GET  /api/forms/qr.svg?item=     the QR of the open form's public link (the stage)
    GET  /api/forms/library          every form in the ledger's sessions (the builder's Copy from)

Public quiz player (`app/webapp/player/server.py`): `GET /play/form?t=`, `GET /play/api/form?t=` and `POST /play/api/form` with `{t, name, answers: {question id: value}, key, phone}`.
