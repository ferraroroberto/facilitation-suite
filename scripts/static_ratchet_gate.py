r"""The gate's static-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.static_ratchet_gate

`scripts/static_ratchet.py` (vendored, never edited here) exits 0 on a pass,
1 on a new finding, 2 on a bad table or baseline and 3 when a detector could
not run (no `npx`, no network on the first `npx`, ruff missing, a timeout).
Exit 3 means nothing was checked, so it must fail the gate as its own state and
never read as a pass or a skip. That mapping lives in `scripts/ratchet_gate.py`;
`verify-before-ship.ps1` calls the wrapper, never the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import ratchet_gate
from scripts.ratchet_gate import NOT_CONFIRMED, REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "static_ratchet.py"

EXIT_NEW, EXIT_CONFIG = 1, 2

# The script bounds each of its two detectors at 600 s itself; this only has to
# outlast both so a hung child is reported by the script's own NOT CONFIRMED.
RUN_TIMEOUT_S = 1300

_OK_LABEL = "no new duplicate block or unused import/variable"
_FAIL_LABELS = {
    EXIT_NEW: "NEW finding or missing baseline",
    EXIT_CONFIG: "bad [static_ratchet] table, baseline or arguments",
    NOT_CONFIRMED: "NOT CONFIRMED -- a detector could not run, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    return ratchet_gate.classify_exit(code, _OK_LABEL, _FAIL_LABELS)


def run(script: Path = SCRIPT) -> int:
    """Run `script` under this interpreter (the venv python, which has ruff), echo
    its output, then the verdict. Returns the gate's exit code."""
    return ratchet_gate.run_ratchet(
        script, stage="static ratchet", ok_label=_OK_LABEL,
        fail_labels=_FAIL_LABELS, timeout_s=RUN_TIMEOUT_S,
    )


if __name__ == "__main__":
    sys.exit(run())
