# remote

<!-- gen:begin feature -->
**Seeded from:** surface()

**How a user reaches it**

- Not declared in `.fleet.toml` or `CLAUDE.md`. Read the driving test: it opens the surface before it calls `surface()`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| chat | `remote.chat` | `tests/e2e/test_story_14_remote.py::test_phone_remote` |
| groups | `remote.groups` | `tests/e2e/test_story_14_remote.py::test_phone_remote` |
| idle | `remote.idle` | `tests/e2e/test_smoke.py::test_shell_loads_and_navigates` |
| live | `remote.live` | `tests/e2e/test_story_14_remote.py::test_phone_remote` |
| quiz-locked | `remote.quiz-locked` | `tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter` |
| unpaired | `remote.unpaired` | `tests/e2e/test_story_14_remote.py::test_phone_remote` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_smoke.py::test_shell_loads_and_navigates tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter tests/e2e/test_story_14_remote.py::test_phone_remote -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
