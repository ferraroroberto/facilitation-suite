"""The live CI workflow describes this repo's one chromium job, not the template it came from."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# Words only the copy-to-adapt template, or a leg this repo does not run, would put in the file.
STALE = re.compile(r"ADAPT|\.template|webkit|firefox|\bphone\b|mypy", re.IGNORECASE)


def test_the_workflow_carries_no_template_or_foreign_leg_wording() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not STALE.findall(text), "ci.yml still reads like the template or names a leg the repo lacks"


def test_the_e2e_step_runs_the_one_chromium_leg() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert len(re.findall(r"--browser\s+(\S+)", text)) == 1
    assert "--browser chromium" in text
