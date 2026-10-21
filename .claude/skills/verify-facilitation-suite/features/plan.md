# plan

<!-- gen:begin feature -->
**Seeded from:** surface()

**How a user reaches it**

- Not declared in `.fleet.toml` or `CLAUDE.md`. Read the driving test: it opens the surface before it calls `surface()`.

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| breakout | `plan.breakout` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| confirm-delete | `plan.confirm-delete` | `tests/e2e/test_story_15_font_select.py::test_stage_font_and_multi_select` |
| editor | `plan.editor` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| empty | `plan.empty` | `tests/e2e/test_smoke.py::test_shell_loads_and_navigates` |
| item-menu | `plan.item-menu` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| multiselect | `plan.multiselect` | `tests/e2e/test_story_15_font_select.py::test_stage_font_and_multi_select` |
| music-editor | `plan.music-editor` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| new-section | `plan.new-section` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| outline | `plan.outline` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| quiz-editor | `plan.quiz-editor` | `tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it` |
| reimport-empty | `plan.reimport-empty` | `tests/e2e/test_story_13_reimport.py::test_review_a_reimport` |
| reimport-review | `plan.reimport-review` | `tests/e2e/test_story_13_reimport.py::test_review_a_reimport` |

**Drive:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_smoke.py::test_shell_loads_and_navigates tests/e2e/test_story_04_plan.py::test_edit_the_plan_and_save_it tests/e2e/test_story_13_reimport.py::test_review_a_reimport tests/e2e/test_story_15_font_select.py::test_stage_font_and_multi_select -q --browser chromium` (the suite boots its own disposable instance).
<!-- gen:end feature -->

## Gotchas

_None recorded yet._ Add one line per trap: the symptom, then the fix. Regeneration never touches this section.
