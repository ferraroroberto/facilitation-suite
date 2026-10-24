r"""The gate's comment-ratchet stage: run the vendored script, map its verdict.

    & .\.venv\Scripts\python.exe -m scripts.comment_ratchet_gate

The script exits 0 (pass, or the stage is not enabled), 1 (a file's `#NNN`
comment-reference count rose, or the baseline is missing), 2 (bad
`[comment_ratchet]` table or baseline) or 3 (a file could not be scanned: an
unterminated string/comment/template/here-string, a Python file `tokenize`
rejects, or undecodable bytes). The exit-code mapping, with 3 as NOT CONFIRMED,
lives in `scripts/ratchet_gate.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import ratchet_gate
from scripts.ratchet_gate import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "comment_ratchet.py"

# A read-only sweep over this repo's tree takes well under a second; this only
# bounds a hung git so it reads as NOT CONFIRMED instead of wedging the gate.
RUN_TIMEOUT_S = 120

_OK_LABEL = "no file above its baseline"
_FAIL_LABELS = {
    1: "a file's comment-reference count rose, or the baseline is missing",
    2: "bad [comment_ratchet] table, baseline or arguments",
    3: "NOT CONFIRMED -- a file could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    return ratchet_gate.classify_exit(code, _OK_LABEL, _FAIL_LABELS)


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return ratchet_gate.run_ratchet(
        script, stage="comment ratchet", ok_label=_OK_LABEL,
        fail_labels=_FAIL_LABELS, timeout_s=RUN_TIMEOUT_S,
    )


if __name__ == "__main__":
    sys.exit(run())
