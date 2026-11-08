"""The atomic writer swaps new content in; a failed write leaves the old file and no temp."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import atomic_write
from src.atomic_write import atomic_write_text


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A folder of its own, so the listing sees only what the writer left."""
    path = tmp_path / "out"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atomic_write.time, "sleep", lambda _s: None)


def test_a_replace_lands_the_new_content(folder: Path) -> None:
    target = folder / "session.yaml"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "name: ü\n")
    assert target.read_bytes() == "name: ü\n".encode()
    assert [p.name for p in folder.iterdir()] == ["session.yaml"]


def test_a_target_held_open_is_written_on_a_later_try(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = folder / "captures.json"
    target.write_text("old", encoding="utf-8")
    real = atomic_write.os.replace
    refusals = [PermissionError("held"), PermissionError("held")]

    def held_twice(src: str, dst: Path) -> None:
        if refusals:
            raise refusals.pop()
        real(src, dst)

    monkeypatch.setattr(atomic_write.os, "replace", held_twice)
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in folder.iterdir()] == ["captures.json"]


def test_a_failed_replace_keeps_the_old_content_and_no_temp(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = folder / ".env"
    target.write_text("old", encoding="utf-8")

    def locked(src: str, dst: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(atomic_write.os, "replace", locked)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in folder.iterdir()] == [".env"]


def test_a_failed_write_keeps_the_old_content_and_no_temp(folder: Path) -> None:
    target = folder / "groups.yaml"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "\udcff")  # a lone surrogate cannot be encoded
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in folder.iterdir()] == ["groups.yaml"]


def test_a_path_under_a_code_folder_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.write_target import CODE_FOLDERS, PROJECT_ROOT

    target = PROJECT_ROOT / CODE_FOLDERS[0] / "never_written.txt"
    with pytest.raises(ValueError, match="code folder"):
        atomic_write_text(target, "x")
    assert not target.exists()
