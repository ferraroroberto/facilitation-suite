"""Is every file of a session folder really on this PC? (epic §5.4)

OneDrive Files On-Demand leaves cloud-only placeholders that look like normal
files until a read blocks on a download. ``os.stat`` does not hydrate a
placeholder, and on Windows it exposes the file attributes that say whether
the content is local. ``pin`` asks OneDrive to keep the whole folder on the
device ("Always keep on this device" = ``attrib +P -U``), which also starts
the download of anything missing.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_PINNED = 0x80000
FILE_ATTRIBUTE_UNPINNED = 0x100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD_ONLY = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


@dataclass
class OfflineReport:
    total: int = 0
    local: int = 0
    cloud_only: list[str] = field(default_factory=list)
    pinned: bool = False
    # "ok" | "cloud_only" | "unknown" (attributes unavailable: not Windows, or unreadable)
    state: str = "unknown"
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _attrs(path: Path) -> Optional[int]:
    try:
        return int(os.stat(path, follow_symlinks=False).st_file_attributes)
    except (OSError, AttributeError):
        return None


def check_folder(folder: Path) -> OfflineReport:
    report = OfflineReport()
    if not folder.is_dir():
        report.detail = "folder not found"
        return report
    if sys.platform != "win32":
        report.detail = "file attributes are only readable on Windows"
        return report
    folder_attrs = _attrs(folder)
    report.pinned = bool(folder_attrs is not None and folder_attrs & FILE_ATTRIBUTE_PINNED)
    unreadable = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = Path(root) / name
            report.total += 1
            a = _attrs(p)
            if a is None:
                unreadable += 1
                continue
            if a & _CLOUD_ONLY:
                report.cloud_only.append(str(p.relative_to(folder)).replace("\\", "/"))
            else:
                report.local += 1
    if unreadable:
        report.state = "unknown"
        report.detail = f"{unreadable} file(s) could not be checked"
    elif report.cloud_only:
        report.state = "cloud_only"
        report.detail = f"{len(report.cloud_only)} of {report.total} file(s) are only in the cloud"
    else:
        report.state = "ok"
        report.detail = f"{report.local} of {report.total} files on this PC"
    return report


def pin_folder(folder: Path) -> tuple[bool, str]:
    """``attrib +P -U`` on the folder and everything under it. Returns (ok, message)."""
    if sys.platform != "win32":
        return False, "pinning is only available on Windows"
    commands = [
        ["attrib", "+P", "-U", str(folder)],
        ["attrib", "+P", "-U", str(folder / "*"), "/S", "/D"],
    ]
    for cmd in commands:
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, encoding="oem", errors="replace",
                timeout=60, creationflags=NO_WINDOW, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("❌ pin: attrib failed to run: %s", exc)
            return False, "could not run attrib"
        if res.returncode != 0:
            logger.warning("⚠️ pin: attrib exit %s: %s", res.returncode, (res.stdout + res.stderr).strip()[:300])
            return False, "attrib refused (is the folder inside OneDrive?)"
    logger.info("📌 pinned %s (always keep on this device)", folder)
    return True, "folder set to always keep on this device"
