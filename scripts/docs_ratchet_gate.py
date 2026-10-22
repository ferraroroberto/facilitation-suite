r"""The gate's docs-ratchet stage: run the vendored script and map its verdict.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

The script exits 0 (pass), 1 (a history marker or the README size rose), 2 (bad
table or baseline) or 3 (the docs could not be read: git unusable, a non-UTF-8
file, a fence or comment that never closes). 3 is its own state, NOT CONFIRMED:
nothing was counted, so it is never a pass and never a skip. The mapping lives
here so a test can pin it, and `verify-before-ship.ps1` calls this module, not
the script. The wrapper exits 0 only on a script exit of 0.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "docs_ratchet.py"

# A Markdown line scan of the README and docs/ takes well under a second; this only
# bounds a hung git so it reads as NOT CONFIRMED instead of wedging the gate.
RUN_TIMEOUT_S = 120

DOCS_FAIL_LABELS = {
    1: "a history marker or the README size rose, or the baseline is missing",
    2: "bad [docs_ratchet] table, baseline or arguments",
    3: "NOT CONFIRMED -- the docs could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == 0:
        return True, "no new history marker and the README within budget"
    return False, DOCS_FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script` under this interpreter, echo its output, then the verdict.
    Returns the gate's exit code."""
    try:
        done = subprocess.run(
            [sys.executable, str(script)], cwd=REPO_ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW, timeout=RUN_TIMEOUT_S, check=False,
        )
        code, output = done.returncode, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = 3, f"{script.name} did not finish in {RUN_TIMEOUT_S}s\n"
    except OSError as exc:
        code, output = 3, f"{script.name} could not start: {exc}\n"
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify_exit(code)
    print(f"{'OK' if ok else 'FAIL'}: docs ratchet: {label}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
