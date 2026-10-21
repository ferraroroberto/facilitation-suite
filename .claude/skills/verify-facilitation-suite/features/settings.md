# settings

<!-- gen:begin feature -->
**Seeded from:** surface() · extra_steps

**How a user reaches it**

- From the `sessions` tab, click `[data-open-settings]`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| obs | `settings.obs` | `tests/e2e/test_story_10_obs.py::test_obs_follows_the_profile` |
| obs-profile | `settings.obs-profile` | `tests/e2e/test_story_10_obs.py::test_obs_follows_the_profile` |
| pane | `settings.pane` | `tests/e2e/test_smoke.py::test_shell_loads_and_navigates` |
| stage-defaults | `settings.stage-defaults` | `tests/e2e/test_story_02_sessions.py::test_create_a_session_and_see_it_ready_list` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_smoke.py::test_shell_loads_and_navigates tests/e2e/test_story_02_sessions.py::test_create_a_session_and_see_it_ready_list tests/e2e/test_story_10_obs.py::test_obs_follows_the_profile -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
