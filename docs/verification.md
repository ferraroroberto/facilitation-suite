# Verification

The pre-ship gate is `& .\scripts\verify-before-ship.ps1`: personal-data guard → byte-compile → ruff → static ratchet (no new duplicate block or unused import/variable beyond `.static-ratchet-baseline.json`; needs Node for `npx jscpd`) → docs ratchet (`README.md` stays under 40 KiB and `README.md` / `docs/**/*.md` gain no history marker, meaning an issue reference, a dated heading or a changelog heading, beyond `.docs-ratchet-baseline.json`; docs state what is true now and why, history lives in git, PRs and issues) → pytest → diff-routed Playwright e2e against a disposable instance.

**Design inventory.** Each e2e story calls `surface(page, "<tab>.<surface>")` (the vendored `tests/e2e/_surface.py`) once the surface is open, at the first test that opens it. It does nothing unless `DESIGN_SURFACE_CAPTURE` is set, which fleet-config's `/design-review measure facilitation-suite --e2e` does against `scripts/design_review_synthetic.py` (the suite's own disposable instance, never the live app). A new tab, sheet or dialog joins the inventory by adding one call to the test that opens it; the stage is never tagged, since it follows the session theme.

**Verify skill.** `.claude/skills/verify-facilitation-suite/` (scope: this repo's root) launches a disposable instance, drives every mapped feature in a real browser and cleans up; the feature map is generated from the `surface()` calls, never hand-written. Claude Code loads it from there. Codex and Pi read `.agents/skills/`, so link it per checkout (the link is gitignored, as is the rest of `.agents/`; skip it if the path already exists):

```powershell
New-Item -ItemType Directory -Path .agents/skills -Force | Out-Null
New-Item -ItemType Junction -Path .agents/skills/verify-facilitation-suite -Target (Resolve-Path .claude/skills/verify-facilitation-suite).Path
```

After adding or renaming a `surface()` call, run the skill's Maintain section; its static half reports a stale map.
