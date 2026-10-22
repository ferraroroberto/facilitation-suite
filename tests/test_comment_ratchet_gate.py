"""The gate's comment-ratchet stage maps the vendored script's exit codes (fleet-config#1517).

`scripts/comment_ratchet.py` exits 3 when a file could not be scanned (an
unterminated string/comment/template, a Python file `tokenize` rejects,
undecodable bytes): nothing was counted. These tests pin that 3 fails as NOT
CONFIRMED -- never a pass, never a skip -- and that the pre-ship gate calls
the wrapper that guarantees it rather than the script. The mapping tests use
fake scripts; the last group copies the real vendored script into a
throwaway git repo so a new issue reference inside a comment is caught by
name. Nothing here reads the live ledger or config.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import comment_ratchet_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_comment_ratchet.py"
    script.write_text(f"import sys\n{body}\n", encoding="utf-8")
    return script


def _exits(tmp_path: Path, code: int, message: str = "") -> Path:
    return _fake_script(tmp_path, f"print({message!r})\nsys.exit({code})")


def test_exit_three_is_its_own_failing_state() -> None:
    verdicts = {code: gate.classify_exit(code) for code in (0, 1, 2, 3, 4, -1)}
    assert [code for code, (ok, _) in verdicts.items() if ok] == [0]
    label = verdicts[3][1]
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()
    assert label not in (verdicts[1][1], verdicts[2][1])


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_exits_nonzero_for_every_script_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    got = gate.run(_exits(tmp_path, code, "from the script"))
    out = capsys.readouterr().out
    assert got == expected
    assert "from the script" in out  # the script's own finding is echoed
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


def test_a_script_that_outlives_the_timeout_is_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "RUN_TIMEOUT_S", 1)
    assert gate.run(_fake_script(tmp_path, "import time\ntime.sleep(30)")) == 1
    out = capsys.readouterr().out
    assert "did not finish" in out
    assert "NOT CONFIRMED" in out


def test_an_interpreter_that_cannot_start_is_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python.exe"))
    assert gate.run(_exits(tmp_path, 0)) == 1
    out = capsys.readouterr().out
    assert "could not start" in out
    assert "NOT CONFIRMED" in out


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stage_lines = [
        line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")
    ]
    stages = [line for line in stage_lines if "comment_ratchet" in line]
    assert len(stages) == 1
    assert "-m scripts.comment_ratchet_gate" in stages[0]
    assert "comment_ratchet.py" not in stages[0]
    # After the docs ratchet, before the unit suite: a comment regression fails fast.
    names = [line.split('"')[1] for line in stage_lines]
    assert names.index("docs ratchet") < names.index("comment ratchet") < names.index(
        "pytest (unit, non-e2e)"
    )


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["comment_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["comment_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["comment_ratchet"]
    assert entry["dest"] == "scripts/comment_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()


# ----------------------------------------------- the real script, on a throwaway repo

_FLEET = '[comment_ratchet]\nenabled = true\n'


def _comment_repo(tmp_path: Path, source: str) -> Path:
    """A git repo holding the vendored script, a `[comment_ratchet]` table and one
    product file `src/sample.py`. The script derives its root from its own location,
    so the copy checks this repo."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "scripts" / "comment_ratchet.py").write_bytes(
        (REPO_ROOT / "scripts" / "comment_ratchet.py").read_bytes()
    )
    (repo / ".fleet.toml").write_text(_FLEET, encoding="utf-8")
    (repo / "src" / "sample.py").write_text(source, encoding="utf-8")
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
    return repo


def _script(repo: Path) -> Path:
    return repo / "scripts" / "comment_ratchet.py"


def _init_baseline(repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_script(repo)), "--init"], cwd=repo, capture_output=True
    )
    assert proc.returncode == 0, proc.stderr


def test_a_new_issue_reference_fails_the_gate_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _comment_repo(tmp_path, "# present-tense why\nvalue = 1\n")
    _init_baseline(repo)
    assert gate.run(_script(repo)) == 0
    capsys.readouterr()

    (repo / "src" / "sample.py").write_text(
        "# fixed in #123\nvalue = 1\n", encoding="utf-8"
    )
    assert gate.run(_script(repo)) == 1
    out = capsys.readouterr().out
    assert "sample.py" in out
    assert "#123" in out


def test_an_unscannable_file_is_not_confirmed_and_fails_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _comment_repo(tmp_path, "# present-tense why\nvalue = 1\n")
    _init_baseline(repo)
    (repo / "src" / "sample.py").write_text("value = 'never closed\n", encoding="utf-8")
    assert gate.run(_script(repo)) == 1
    out = capsys.readouterr().out
    assert "sample.py" in out
    assert "NOT CONFIRMED" in out
