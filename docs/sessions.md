# Sessions

A session is a folder, by default `<session_root>\<workshop>\<session>\`:

```
session.yaml      the plan (schema v1) — human-readable, safe to edit by hand
slides/           slide PNGs + slides.json (titles, notes, fingerprints, detected OBS profile)
roster.xlsx       participants (optional)
audio/            music files the items play (optional; copied in from the Plan tab)
groups.yaml       breakout groups (optional)
aliases.yaml      names that are one person, written by Results (optional; see results.md)
playbook.md       this session's instructions for the analysis afterwards (optional; never required to go live)
theme.css         per-session stage theme override (optional; applied after the stage lettering)
                  (session.yaml `theme:` names the base: a repo theme or `library/<name>` from the stage library)
live/             chat.jsonl, events.jsonl, quiz.jsonl, captures/ — append-only during the session
exports/          session.pdf, report.xlsx, zoom-reconciliation.json, zoom-rooms-*.csv
```

The Sessions tab creates, duplicates (plan, slides, roster, theme, music, playbook — never live data) and adds existing folders, and shows a readiness checklist. Clicking a session opens it full screen (folder, plan, its **Session settings**, readiness, go-live buttons) with the tabs still in reach; its **×**, **Esc** or the browser's Back returns to the list where you left it, and the session stays the one the Plan, Groups and Results tabs work on. `/#sessions/<id>` links straight to an open session. **Session settings** is that session's own: its title, date and time, duration, stage language and stage hint, edited in place and saved to `session.yaml` as you leave each field (Enter too; Esc puts the field back), then its stage look (theme and lettering). What is common to every session stays on the Settings page. **Files offline** checks OneDrive's file attributes without downloading anything and can pin the folder ("Always keep on this device"). Unknown keys in `session.yaml` survive a load → save round-trip. An item's `id` is letters, digits, `-` and `_` (up to 80 — it names the item's capture files); one written by hand outside that, or a Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1`–`com9`, `lpt1`–`lpt9`, any case), is made to fit on load (`act Q1` → `act-Q1`, `con` → `con-x`, logged ⚠️), and an empty or repeated one gets a fresh id. No database ever lives in the session folder.

**Playbook.** The open session shows a **Playbook** card after the Plan card: the session folder's `playbook.md`, rendered (headings, lists, bold and italic, links, tables, quotes, code). A long playbook is collapsed to a few lines until **Show all**. **Edit** opens the file in the default editor on this PC, and **Reload** reads it again after a save; the app never edits markdown itself. A session without one shows **Create playbook**, which writes a short generic template (`## Purpose`, `## What to analyse`, `## Output format`, `## Rules`, `## People to leave out`) and never overwrites an existing file (the API answers 409). The server renders the markdown (`src/sessions/playbook.py`) after escaping every character of the file: raw HTML shows as text, and a link keeps only an http(s), mailto or relative target. A real playbook is per session and stays in its session folder; the repo's gitignore and the gate's personal-data guard refuse a tracked `playbook.md` outside `tests/fixtures/`.
