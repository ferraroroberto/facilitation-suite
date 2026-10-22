r"""The gate's comment-ratchet stage: run the vendored script, map its verdict.

    & .\.venv\Scripts\python.exe -m scripts.comment_ratchet_gate

The script exits 0 (pass, or the stage is not enabled), 1 (a file's `#NNN`
comment-reference count rose, or the baseline is missing), 2 (bad
`[comment_ratchet]` table or baseline) or 3 (a file could not be scanned: an
unterminated string/comment/template/here-string, a Python file `tokenize`
rejects, or undecodable bytes). 3 is its own state, NOT CONFIRMED: nothing was
counted, so it is never a pass and never a skip. The mapping lives here so a
test can pin it, and `verify-before-ship.ps1` calls this module, not the
script. The wrapper exits 0 only on a script exit of 0.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "comment_ratchet.py"

# A read-only sweep over this repo's tree takes well under a second; this only
# bounds a hung git so it reads as NOT CONFIRMED instead of wedging the gate.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    1: "a file's comment-reference count rose, or the baseline is missing",
    2: "bad [comment_ratchet] table, baseline or arguments",
    3: "NOT CONFIRMED -- a file could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == 0:
        return True, "no file above its baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script` under this interpreter, echo its output, then the verdict.
    Returns the gate's exit code."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=RUN_TIMEOUT_S, check=False,
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = 3, f"{script.name} did not finish in {RUN_TIMEOUT_S}s\n"
    except OSError as exc:
        code, output = 3, f"{script.name} could not start: {exc}\n"
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify_exit(code)
    print(f"{'OK' if ok else 'FAIL'}: comment ratchet: {label}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
