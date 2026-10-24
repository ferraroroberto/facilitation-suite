r"""The gate's docs-ratchet stage: run the vendored script and map its verdict.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

The script exits 0 (pass), 1 (a history marker or the README size rose), 2 (bad
table or baseline) or 3 (the docs could not be read: git unusable, a non-UTF-8
file, a fence or comment that never closes). The exit-code mapping, with 3 as
NOT CONFIRMED, lives in `scripts/ratchet_gate.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import ratchet_gate
from scripts.ratchet_gate import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "docs_ratchet.py"

# A Markdown line scan of the README and docs/ takes well under a second; this only
# bounds a hung git so it reads as NOT CONFIRMED instead of wedging the gate.
RUN_TIMEOUT_S = 120

_OK_LABEL = "no new history marker and the README within budget"
_FAIL_LABELS = {
    1: "a history marker or the README size rose, or the baseline is missing",
    2: "bad [docs_ratchet] table, baseline or arguments",
    3: "NOT CONFIRMED -- the docs could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    return ratchet_gate.classify_exit(code, _OK_LABEL, _FAIL_LABELS)


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return ratchet_gate.run_ratchet(
        script, stage="docs ratchet", ok_label=_OK_LABEL,
        fail_labels=_FAIL_LABELS, timeout_s=RUN_TIMEOUT_S,
    )


if __name__ == "__main__":
    sys.exit(run())
