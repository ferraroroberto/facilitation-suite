# Sessions

A session is a folder, by default `<session_root>\<workshop>\<session>\`:

```
session.yaml      the plan (schema v1) — human-readable, safe to edit by hand
slides/           slide PNGs + slides.json (titles, notes, fingerprints, detected OBS profile)
roster.xlsx       participants (optional)
audio/            music files the items play (optional; copied in from the Plan tab)
groups.yaml       breakout groups (optional)
aliases.yaml      names that are one person, written by Results (optional; see results.md)
theme.css         per-session stage theme override (optional; applied after the stage lettering)
                  (session.yaml `theme:` names the base: a repo theme or `library/<name>` from the stage library)
live/             chat.jsonl, events.jsonl, quiz.jsonl, captures/ — append-only during the session
exports/          session.pdf, report.xlsx, zoom-reconciliation.json, zoom-rooms-*.csv
```

The Sessions tab creates, duplicates (plan, slides, roster, theme, music — never live data) and adds existing folders, and shows a readiness checklist. Clicking a session opens it full screen (folder, plan, its **Session settings**, readiness, go-live buttons) with the tabs still in reach; its **×**, **Esc** or the browser's Back returns to the list where you left it, and the session stays the one the Plan, Groups and Results tabs work on. `/#sessions/<id>` links straight to an open session. **Session settings** is that session's own: its title, date and time, duration, stage language and stage hint, edited in place and saved to `session.yaml` as you leave each field (Enter too; Esc puts the field back), then its stage look (theme and lettering). What is common to every session stays on the Settings page. **Files offline** checks OneDrive's file attributes without downloading anything and can pin the folder ("Always keep on this device"). Unknown keys in `session.yaml` survive a load → save round-trip. An item's `id` is letters, digits, `-` and `_` (up to 80 — it names the item's capture files); one written by hand outside that, or a Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1`–`com9`, `lpt1`–`lpt9`, any case), is made to fit on load (`act Q1` → `act-Q1`, `con` → `con-x`, logged ⚠️), and an empty or repeated one gets a fresh id. No database ever lives in the session folder.
