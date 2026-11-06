"""``outside_code`` refuses a runtime write target under the app's own code folders."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import PROJECT_ROOT
from src.write_target import CODE_FOLDERS, outside_code


@pytest.mark.parametrize("folder", CODE_FOLDERS)
def test_a_target_under_a_code_folder_is_refused(folder: str) -> None:
    with pytest.raises(ValueError, match=f"code folder {folder}/"):
        outside_code(PROJECT_ROOT / folder / "nested" / "run.log")


def test_a_dotted_path_back_into_a_code_folder_is_refused() -> None:
    with pytest.raises(ValueError):
        outside_code(PROJECT_ROOT / "data" / ".." / "src" / "x.json")


def test_a_relative_path_is_read_from_the_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT)
    with pytest.raises(ValueError):
        outside_code("app/out.json")


def test_data_the_session_folders_and_temp_pass_through_unchanged(tmp_path: Path) -> None:
    for target in (PROJECT_ROOT / "data" / "tmp", tmp_path / "sessions" / "w" / "s", PROJECT_ROOT / "tests" / "x"):
        assert outside_code(target) == target
    assert outside_code(str(tmp_path)) == tmp_path
