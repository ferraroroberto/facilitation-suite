# Breakout groups

The **Groups** tab makes the 1-2-4-all breakout rooms (the algorithm ported from `facilitation-shuffle`, unchanged):

1. **Import roster** (an Excel file) — the first sheet, columns by header in any order: `name` (required; without a `name` header the first column is used), `role`, `company`, `country`, `present`, `email` (optional). The file is copied into the session folder as `roster.xlsx`; the original is never edited.
2. **Mark who is here** with the switches (they win over the file's `present` column and are saved in `groups.yaml`).
3. **Shuffle** — three rounds: **pairs** (an odd number gives one trio), **groups of 4 · A** (whole pairs merged, never split; 4k+2 people give one room of six), **groups of 4 · B** (re-mixed into rooms of four — a room of three or five takes the remainder, never fewer than three — keeping as few people as possible with someone from their round-A room — up to 120 000 tries). The note under the tabs says how well round B mixed.
4. **Copy rooms for Zoom** (`Room 1: Ana, Sam` lines to paste while assigning rooms by hand) or **Export Zoom pre-assign CSV** (`Pre-assign Room Name,Email Address`, also kept in `exports/`) — only when everyone present has an email; otherwise the tab says who is missing one.
5. **Add reveal slide** puts a "Who are you with?" item into the Plan tab's unsaved edits, right after the selected item (then Save there): the stage shows every room of that round, numbered.

If presence changes after a shuffle, the tab and the readiness checklist say so until you shuffle again.
