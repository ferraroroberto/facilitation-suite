"""Where a runtime write may land: anywhere except the app's own code folders.

Sessions, logs, caches and exports are written to paths the caller hands in (a session
folder, an export target), so a static check cannot tell where they point. ``outside_code``
is the one declared seam: a write that builds its target through it is refused when the path
lies under ``src/``, ``app/``, ``scripts/``, ``hooks/`` or ``skills/``, so a mis-set session
root can never put runtime files next to the code. The runtime-write ratchet lists it in
``.fleet.toml [runtime_write_ratchet] safe_calls``.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.config import PROJECT_ROOT

#: The ratchet's code folders (the scaffold default), the places runtime output must never land.
CODE_FOLDERS = ("src", "app", "scripts", "hooks", "skills")


def outside_code(path: Path | str) -> Path:
    """``path`` as a ``Path``; ``ValueError`` when it lies under one of the app's code folders.

    Lexical, so the hot paths (every chat append) pay no filesystem call.
    """
    target = Path(os.path.abspath(path))
    for name in CODE_FOLDERS:
        if target.is_relative_to(PROJECT_ROOT / name):
            raise ValueError(f"refusing to write under the code folder {name}/: {target}")
    return Path(path)
