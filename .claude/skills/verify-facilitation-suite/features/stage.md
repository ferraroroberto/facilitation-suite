# stage

<!-- gen:begin feature -->
**Seeded from:** key views

**How a user reaches it**

- Open the route `/stage` (what Zoom sees — session theme, not the fleet design).

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| what Zoom sees — session theme, not the fleet design | - | none: see Drive below |

**Drive:** no `surface()` name covers this view yet, so no test drives it. Launch an instance and reach it as described above; to make it testable, add a test that opens it and calls `surface()`.
<!-- gen:end feature -->

## Gotchas

- **No node drives `/stage`, by design.** It is never tagged with `surface()` because it follows the session theme (`themes/*.css`), not the fleet design. Reach it on the disposable instance as `/stage`.
