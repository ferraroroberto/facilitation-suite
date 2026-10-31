"""The gate's wrapped ratchet stages (static, comment, docs) map the vendored scripts' exit codes.

Each script exits 3 when it could not check what it reads (a detector that could not
run, an unscannable source file, an unreadable or unclosed doc): nothing was counted.
These tests pin that 3 fails as NOT CONFIRMED -- never a pass, never a skip -- and that
the pre-ship gate calls the wrapper that guarantees it rather than the script. The
mapping tests use fake scripts; the last group copies the real vendored script into a
throwaway git repo so a new issue reference and an unreadable file are caught by name
(the static ratchet needs `npx` there, so it has no row in that group). Nothing here
reads the live ledger or config.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from scripts import comment_ratchet_gate, docs_ratchet_gate, static_ratchet_gate
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Stage:
    """One wrapped ratchet: its gate module, its place in the pre-ship gate, and the
    throwaway-repo file the real script reads."""

    key: str  # the `.fleet.toml` table and the script's stem
    gate: ModuleType
    name: str  # the `Invoke-Stage` name
    after: str  # the stage it runs after
    before: str  # the stage it runs before
    file: str = ""  # empty: the stage has no throwaway-repo group
    clean: str = ""
    marked: str = ""  # `clean`, plus an issue reference
    unreadable: str = ""  # content the script cannot scan: exit 3


STAGES = [
    Stage("static_ratchet", static_ratchet_gate, "static ratchet", "ruff", "docs ratchet"),
    Stage(
        "comment_ratchet", comment_ratchet_gate, "comment ratchet", "docs ratchet",
        "pytest (unit, non-e2e)", "src/sample.py",
        "# present-tense why\nvalue = 1\n", "# fixed in #123\nvalue = 1\n",
        "value = 'never closed\n",
    ),
    Stage(
        "docs_ratchet", docs_ratchet_gate, "docs ratchet", "static ratchet",
        "comment ratchet", "README.md",
        "# Title\n\nPresent-tense prose.\n", "# Title\n\nFixed in #123.\n",
        "# Title\n\n```\nnever closed\n",
    ),
]

stages = pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.key)
repo_stages = pytest.mark.parametrize("stage", [s for s in STAGES if s.file], ids=lambda s: s.key)
# Every vendored ratchet the gate runs: the wrapped stages plus the one called directly.
RATCHETS = [*(s.key for s in STAGES), "conventions_ratchet"]


def _fake_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_ratchet.py"
    script.write_text(f"import sys\n{body}\n", encoding="utf-8")
    return script


def _exits(tmp_path: Path, code: int, message: str = "") -> Path:
    return _fake_script(tmp_path, f"print({message!r})\nsys.exit({code})")


@stages
def test_exit_three_is_its_own_failing_state(stage: Stage) -> None:
    verdicts = {code: stage.gate.classify_exit(code) for code in (0, 1, 2, 3, 4, -1)}
    assert [code for code, (ok, _) in verdicts.items() if ok] == [0]
    label = verdicts[3][1]
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()
    assert label not in (verdicts[1][1], verdicts[2][1])


@stages
@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_exits_nonzero_for_every_script_failure(
    stage: Stage, tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    got = stage.gate.run(_exits(tmp_path, code, "from the script"))
    out = capsys.readouterr().out
    assert got == expected
    assert "from the script" in out  # the script's own finding is echoed
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


@stages
def test_a_script_that_outlives_the_timeout_is_not_confirmed(
    stage: Stage, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stage.gate, "RUN_TIMEOUT_S", 1)
    assert stage.gate.run(_fake_script(tmp_path, "import time\ntime.sleep(30)")) == 1
    out = capsys.readouterr().out
    assert "did not finish" in out
    assert "NOT CONFIRMED" in out


@stages
def test_an_interpreter_that_cannot_start_is_not_confirmed(
    stage: Stage, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python.exe"))
    assert stage.gate.run(_exits(tmp_path, 0)) == 1
    out = capsys.readouterr().out
    assert "could not start" in out
    assert "NOT CONFIRMED" in out


@stages
def test_the_wrapper_runs_under_the_venv_interpreter(
    stage: Stage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    real = subprocess.run

    def spy(argv: list[str], **kwargs: object) -> object:
        seen.append(argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    stage.gate.run(_exits(tmp_path, 0))
    assert seen[0][0] == sys.executable  # the python that has ruff, not PATH's


@stages
def test_the_pre_ship_gate_calls_the_wrapper_not_the_script(stage: Stage) -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stage_lines = [
        line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")
    ]
    mine = [line for line in stage_lines if stage.key in line]
    assert len(mine) == 1
    assert f"-m scripts.{stage.key}_gate" in mine[0]
    assert f"{stage.key}.py" not in mine[0]
    names = [line.split('"')[1] for line in stage_lines]
    assert names.index(stage.after) < names.index(stage.name) < names.index(stage.before)


@pytest.mark.parametrize("key", RATCHETS)
def test_the_ratchet_is_declared_and_its_script_is_vendored(key: str) -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet[key]["enabled"] is True
    assert (REPO_ROOT / fleet[key]["baseline"]).is_file()
    entry = fleet["vendored"][key]
    assert entry["dest"] == f"scripts/{key}.py"
    assert (REPO_ROOT / entry["dest"]).is_file()


# ----------------------------------------------- the real script, on a throwaway repo


def _repo(tmp_path: Path, stage: Stage) -> Path:
    """A git repo holding the vendored script, the stage's table and one checked file.
    The script derives its root from its own location, so the copy checks this repo."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / f"{stage.key}.py").write_bytes(
        (REPO_ROOT / "scripts" / f"{stage.key}.py").read_bytes()
    )
    (repo / ".fleet.toml").write_text(f"[{stage.key}]\nenabled = true\n", encoding="utf-8")
    _write(repo, stage, stage.clean)
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(
            ["git", *argv], cwd=repo, check=True, capture_output=True, creationflags=NO_WINDOW
        )
    return repo


def _write(repo: Path, stage: Stage, content: str) -> None:
    target = repo / stage.file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _script(repo: Path, stage: Stage) -> Path:
    return repo / "scripts" / f"{stage.key}.py"


def _init_baseline(repo: Path, stage: Stage) -> None:
    proc = subprocess.run(
        [sys.executable, str(_script(repo, stage)), "--init"],
        cwd=repo, capture_output=True, creationflags=NO_WINDOW,
    )
    assert proc.returncode == 0, proc.stderr


@repo_stages
def test_a_new_issue_reference_fails_the_gate_by_name(
    stage: Stage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, stage)
    _init_baseline(repo, stage)
    assert stage.gate.run(_script(repo, stage)) == 0
    capsys.readouterr()

    _write(repo, stage, stage.marked)
    assert stage.gate.run(_script(repo, stage)) == 1
    out = capsys.readouterr().out
    assert Path(stage.file).name in out
    assert "#123" in out


@repo_stages
def test_an_unreadable_file_is_not_confirmed_and_fails_the_gate(
    stage: Stage, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, stage)
    _init_baseline(repo, stage)
    _write(repo, stage, stage.unreadable)
    assert stage.gate.run(_script(repo, stage)) == 1
    out = capsys.readouterr().out
    assert Path(stage.file).name in out
    assert "NOT CONFIRMED" in out
