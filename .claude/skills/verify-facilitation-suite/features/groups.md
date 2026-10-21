# groups

<!-- gen:begin feature -->
**Seeded from:** surface()

**How a user reaches it**

- Not declared in `.fleet.toml` or `CLAUDE.md`. Read the driving test: it opens the surface before it calls `surface()`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| empty | `groups.empty` | `tests/e2e/test_story_09_groups.py::test_shuffle_breakout_rounds` |
| rooms | `groups.rooms` | `tests/e2e/test_story_09_groups.py::test_shuffle_breakout_rounds` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_story_09_groups.py::test_shuffle_breakout_rounds -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
