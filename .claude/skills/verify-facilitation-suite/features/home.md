# home

<!-- gen:begin feature -->
**Seeded from:** key views

**How a user reaches it**

- Open the route `/` (Sessions · Plan · Groups · Results tabs, Settings behind the header gear).

| Sub-feature | Surface name | Driving test node(s) |
| --- | --- | --- |
| Sessions · Plan · Groups · Results tabs, Settings behind the header gear | - | none: see Drive below |

**Drive:** no `surface()` name covers this view yet, so no test drives it. Launch an instance and reach it as described above; to make it testable, add a test that opens it and calls `surface()`.
<!-- gen:end feature -->

## Gotchas

- **`/` is the shell, not a feature of its own.** Its tabs are the `sessions`, `plan`, `groups` and `results` views and the Settings pane behind the header gear, each with its own file and nodes; this entry exists only because `## UX surface` lists the route.
