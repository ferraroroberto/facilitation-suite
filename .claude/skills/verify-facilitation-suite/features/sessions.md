# sessions

<!-- gen:begin feature -->
**Seeded from:** surface()

**How a user reaches it**

- Not declared in `.fleet.toml` or `CLAUDE.md`. Read the driving test: it opens the surface before it calls `surface()`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| detail | `sessions.detail` | `tests/e2e/test_story_02_sessions.py::test_create_a_session_and_see_it_ready_list` |
| empty | `sessions.empty` | `tests/e2e/test_smoke.py::test_shell_loads_and_navigates` |
| new-session | `sessions.new-session` | `tests/e2e/test_story_02_sessions.py::test_create_a_session_and_see_it_ready_list` |
| phone-empty | `sessions.phone-empty` | `tests/e2e/test_smoke.py::test_shell_loads_and_navigates` |
| stage-font | `sessions.stage-font` | `tests/e2e/test_story_15_font_select.py::test_stage_font_and_multi_select` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_smoke.py::test_shell_loads_and_navigates tests/e2e/test_story_02_sessions.py::test_create_a_session_and_see_it_ready_list tests/e2e/test_story_15_font_select.py::test_stage_font_and_multi_select -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
