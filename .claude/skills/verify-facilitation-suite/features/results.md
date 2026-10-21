# results

<!-- gen:begin feature -->
**Seeded from:** surface()

**How a user reaches it**

- Not declared in `.fleet.toml` or `CLAUDE.md`. Read the driving test: it opens the surface before it calls `surface()`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| detail | `results.detail` | `tests/e2e/test_story_12_results.py::test_results_after_the_session` |
| phone | `results.phone` | `tests/e2e/test_story_12_results.py::test_results_after_the_session` |
| reconcile-warning | `results.reconcile-warning` | `tests/e2e/test_story_12_results.py::test_results_after_the_session` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_story_12_results.py::test_results_after_the_session -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
