"""Run a vendored ratchet script and map its verdict, for the pre-ship gate's wrappers.

Each stage has a thin wrapper (`scripts/<stage>_ratchet_gate.py`) that names its script,
its labels and its timeout and calls `run_ratchet`. The vendored scripts exit 0 (pass),
1 (a finding rose or the baseline is missing), 2 (a bad table or baseline) or 3 (a
detector or a file could not be read). 3 is its own state, NOT CONFIRMED: nothing was
checked, so it is never a pass and never a skip. The mapping lives here once so the
tests can pin it, and `verify-before-ship.ps1` calls the wrappers, never the scripts.
A wrapper exits 0 only on a script exit of 0.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent

NOT_CONFIRMED = 3


def classify_exit(code: int, ok_label: str, fail_labels: Mapping[int, str]) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == 0:
        return True, ok_label
    return False, fail_labels.get(code, f"unexpected exit {code}")


def run_ratchet(
    script: Path, *, stage: str, ok_label: str, fail_labels: Mapping[int, str], timeout_s: int
) -> int:
    """Run `script` under this interpreter (the venv python), echo its output, then the
    verdict. Returns the gate's exit code."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=timeout_s, check=False,
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = NOT_CONFIRMED, f"{script.name} did not finish in {timeout_s}s\n"
    except OSError as exc:
        code, output = NOT_CONFIRMED, f"{script.name} could not start: {exc}\n"
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify_exit(code, ok_label, fail_labels)
    print(f"{'OK' if ok else 'FAIL'}: {stage}: {label}")
    return 0 if ok else 1
