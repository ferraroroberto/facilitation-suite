# facilitation-suite feature map

One file per view. Seeded from the repo's committed inventories, never hand-written: the e2e suite's `surface()` names first, then `[[design.review.extra_steps]]`, then the `## UX surface` key views in `CLAUDE.md`. Regenerate with project-scaffolding's `scripts/gen_verify_skill.py`; add traps to a view's `## Gotchas`, the only hand-written part.

<!-- gen:begin index -->
| Feature | Sub-features | Driving nodes | Seeded from |
| --- | --- | --- | --- |
| [groups](groups.md) | 2 | 1 | surface() |
| [home](home.md) | 1 | 0 | key views |
| [plan](plan.md) | 12 | 4 | surface() |
| [player](player.md) | 2 | 2 | surface() |
| [presenter](presenter.md) | 8 | 4 | surface() · key views |
| [remote](remote.md) | 6 | 3 | surface() |
| [results](results.md) | 3 | 1 | surface() |
| [sessions](sessions.md) | 5 | 3 | surface() |
| [settings](settings.md) | 4 | 3 | surface() · extra_steps |
| [stage](stage.md) | 1 | 0 | key views |

_No declared e2e surfaces; every `surface()` call was mapped._
<!-- gen:end index -->
