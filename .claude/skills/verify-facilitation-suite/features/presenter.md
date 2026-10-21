# presenter

<!-- gen:begin feature -->
**Seeded from:** surface() · key views

**How a user reaches it**

- Open the route `/presenter` (the live cockpit).

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| break-timer | `presenter.break-timer` | `tests/e2e/test_story_05_live.py::test_stage_and_presenter_stay_in_sync` |
| capture | `presenter.capture` | `tests/e2e/test_story_07_capture.py::test_capture_a_word_cloud_and_freeze_it` |
| chat | `presenter.chat` | `tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter` |
| confirm-reset | `presenter.confirm-reset` | `tests/e2e/test_story_05_live.py::test_stage_and_presenter_stay_in_sync` |
| keys | `presenter.keys` | `tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter` |
| map | `presenter.map` | `tests/e2e/test_story_08_map.py::test_people_land_on_the_map` |
| opening | `presenter.opening` | `tests/e2e/test_story_05_live.py::test_stage_and_presenter_stay_in_sync` |
| quiz-live | `presenter.quiz-live` | `tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_story_05_live.py::test_stage_and_presenter_stay_in_sync tests/e2e/test_story_06_chat.py::test_a_simulated_burst_reaches_the_presenter tests/e2e/test_story_07_capture.py::test_capture_a_word_cloud_and_freeze_it tests/e2e/test_story_08_map.py::test_people_land_on_the_map -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
