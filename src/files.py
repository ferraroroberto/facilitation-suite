"""Shared file-staging helper: copy a user-picked file into an app-owned folder.

Both the session's music import (``src/music/library.py``) and the facilitator's
stage library (``src/library.py``) need the same thing: copy a file the facilitator
picked on this PC into a folder the app manages, reusing it when the same bytes are
already there, else disambiguating with ``-2``, ``-3``... so an earlier file is never
replaced.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path
from typing import Optional


def copy_into(folder: Path, source: Path, stem: str, suffix: Optional[str] = None) -> tuple[Path, bool]:
    """Copy ``source`` into ``folder`` as ``<stem><suffix>``.

    ``suffix`` defaults to ``source.suffix`` (case preserved); pass it explicitly to
    normalize the extension (e.g. lower-cased). A destination that already holds the
    same bytes is reused; a different file under that name gets ``-2``, ``-3``... The
    copy itself goes through a temp file + atomic replace so a reader never sees a
    partially-written file.

    Returns ``(dest, True)`` when a new copy was written, ``(dest, False)`` when an
    existing identical file was reused.
    """
    suffix = source.suffix if suffix is None else suffix
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stem}{suffix}"
    n = 1
    while dest.exists() and not filecmp.cmp(source, dest, shallow=False):
        n += 1
        dest = folder / f"{stem}-{n}{suffix}"
    if dest.exists():
        return dest, False
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(source, tmp)
    tmp.replace(dest)
    return dest, True
