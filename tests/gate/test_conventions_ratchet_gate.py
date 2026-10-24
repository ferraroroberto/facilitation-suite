"""The gate's conventions-ratchet stage: declared, wired, and failing by rule name.

`scripts/conventions_ratchet.py` (vendored, never edited here) exits 3 when `git` is
unusable or a tracked `.py` file does not parse: nothing was fully checked. The gate calls
the script directly and `Invoke-Stage` fails on any nonzero exit, so 3 fails as NOT
CONFIRMED; these tests pin that wiring and run the real script on a throwaway git repo so a
seeded violation is caught by rule name. Nothing here reads the live ledger or config.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "conventions_ratchet.py"

_FLEET = (
    "[conventions_ratchet]\n"
    "enabled = true\n"
    'rules = ["no-window", "logging-not-print", "no-drive-path"]\n'
)


def _stage_lines() -> list[str]:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    return [line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")]


def test_the_pre_ship_gate_runs_the_script_unrouted_after_dead_code() -> None:
    stage_lines = _stage_lines()
    stages = [line for line in stage_lines if "conventions_ratchet" in line]
    assert len(stages) == 1
    assert 'scripts/conventions_ratchet.py"' in stages[0]
    # Called directly, so a nonzero exit (3 included) fails the stage; no path routing.
    assert "if " not in stages[0]
    names = [line.split('"')[1] for line in stage_lines]
    assert names.index("dead-code ratchet") < names.index("conventions ratchet") < names.index(
        "pytest (unit, non-e2e)"
    )


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["conventions_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["conventions_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["conventions_ratchet"]
    assert entry["dest"] == "scripts/conventions_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()


# ----------------------------------------------- the real script, on a throwaway repo


def _convention_repo(tmp_path: Path, source: str) -> Path:
    """A git repo holding the vendored script, a `[conventions_ratchet]` table and one
    library file `src/sample.py`. The script derives its root from its own location."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "scripts" / "conventions_ratchet.py").write_bytes(SCRIPT.read_bytes())
    (repo / ".fleet.toml").write_text(_FLEET, encoding="utf-8")
    (repo / "src" / "sample.py").write_text(source, encoding="utf-8")
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True, creationflags=NO_WINDOW)
    return repo


def _run(repo: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "conventions_ratchet.py"), *flags],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW, check=False,
    )


def test_a_new_print_in_library_code_fails_by_rule_name(tmp_path: Path) -> None:
    repo = _convention_repo(tmp_path, "value = 1\n")
    assert _run(repo, "--init").returncode == 0
    assert _run(repo).returncode == 0

    (repo / "src" / "sample.py").write_text("print('debug')\nvalue = 1\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 1
    assert "[logging-not-print]" in proc.stdout
    assert "sample.py" in proc.stdout


def test_an_unparseable_file_is_not_confirmed_exit_three(tmp_path: Path) -> None:
    repo = _convention_repo(tmp_path, "value = 1\n")
    assert _run(repo, "--init").returncode == 0
    (repo / "src" / "sample.py").write_text("def broken(:\n", encoding="utf-8")
    assert _run(repo).returncode == 3
