"""Per-slide image analysis: fingerprints and the OBS profile (epic §6).

**Fingerprint.** A 64-bit difference hash (dHash) of the PNG — stable under
re-export and tiny rendering noise, different when the drawing changes —
plus SHA-1 of the title and of the notes, so a re-import can say *what*
changed.

**OBS profile.** The decks reserve the camera's place on the slide: a flat
grey box (or an empty flat area) where OBS later composites the camera.

- a flat box / empty area covering the right part of the slide → ``camera_strip``
- a small flat box in the top-right corner → ``camera_pip``
- no reserved area → ``screen_only``
- anything else → ``None`` (unsure: flagged in the plan for a manual pick)

A line ``[obs: pip|strip|screen]`` in the slide notes overrides detection.
The detected zone (fractions of the slide) is kept so the stage can leave the
same area empty.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

W, H = 240, 135  # analysis grid (16:9)

NOTES_OVERRIDE = re.compile(r"\[\s*obs\s*:\s*(pip|strip|screen|camera_pip|camera_strip|screen_only)\s*\]", re.I)
_OVERRIDE_MAP = {"pip": "camera_pip", "strip": "camera_strip", "screen": "screen_only"}


@dataclass
class ProfileGuess:
    profile: Optional[str]
    source: str  # "notes" | "detected" | "unsure"
    zone: Optional[tuple[float, float, float, float]] = None  # x0, y0, x1, y1 as fractions


def dhash(path: Path) -> str:
    with Image.open(path) as im:
        small = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.get_flattened_data())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()[:12]


def _quant(c: tuple[int, int, int]) -> tuple[int, int, int]:
    return (c[0] // 6, c[1] // 6, c[2] // 6)


def _runs(values: list[int], threshold: float) -> Optional[tuple[int, int]]:
    """The longest contiguous run of indexes whose value ≥ threshold."""
    best: Optional[tuple[int, int]] = None
    start = None
    for i, v in enumerate(values + [-1]):
        if v >= threshold and start is None:
            start = i
        elif v < threshold and start is not None:
            if best is None or (i - start) > (best[1] - best[0]):
                best = (start, i)
            start = None
    return best


def _flat_zone(px: list[tuple[int, int, int]], color: tuple[int, int, int]) -> Optional[tuple[int, int, int, int, float]]:
    mask = [1 if _quant(c) == color else 0 for c in px]
    cols = [sum(mask[y * W + x] for y in range(H)) for x in range(W)]
    rows = [sum(mask[y * W + x] for x in range(W)) for y in range(H)]
    cx = _runs(cols, H * 0.15)
    cy = _runs(rows, W * 0.10)
    if not cx or not cy:
        return None
    x0, x1 = cx
    y0, y1 = cy
    area = (x1 - x0) * (y1 - y0)
    if area < W * H * 0.02:
        return None
    filled = sum(mask[y * W + x] for y in range(y0, y1) for x in range(x0, x1))
    return x0, y0, x1, y1, filled / area


def detect_profile(path: Path, notes: str = "") -> ProfileGuess:
    m = NOTES_OVERRIDE.search(notes or "")
    if m:
        key = m.group(1).lower()
        return ProfileGuess(_OVERRIDE_MAP.get(key, key), "notes")

    with Image.open(path) as im:
        small = im.convert("RGB").resize((W, H), Image.Resampling.BOX)
    px = list(small.get_flattened_data())
    counts = Counter(_quant(c) for c in px)
    background = counts.most_common(1)[0][0]

    # 1) an explicit flat box in another colour (the grey camera placeholder)
    for color, n in counts.most_common(6)[1:]:
        if n < W * H * 0.02:
            break
        zone = _flat_zone(px, color)
        if not zone or zone[4] < 0.9:
            continue
        x0, y0, x1, y1, _fill = zone
        fx0, fy0, fx1, fy1 = x0 / W, y0 / H, x1 / W, y1 / H
        rel = (round(fx0, 3), round(fy0, 3), round(fx1, 3), round(fy1, 3))
        w, h = fx1 - fx0, fy1 - fy0
        if fx0 >= 0.5 and w >= 0.18 and h >= 0.35:
            return ProfileGuess("camera_strip", "detected", rel)
        if fx0 >= 0.6 and fy0 <= 0.2 and w < 0.35 and h < 0.4:
            return ProfileGuess("camera_pip", "detected", rel)

    # 2) no box: is the right 44% simply empty background?
    right = [px[y * W + x] for y in range(H) for x in range(int(W * 0.56), W)]
    if sum(1 for c in right if _quant(c) == background) / len(right) > 0.985:
        return ProfileGuess("camera_strip", "detected", (0.56, 0.0, 1.0, 1.0))

    # 3) content spread across the slide, nothing reserved
    left = [px[y * W + x] for y in range(H) for x in range(0, int(W * 0.44))]
    busy_right = sum(1 for c in right if _quant(c) != background) / len(right)
    busy_left = sum(1 for c in left if _quant(c) != background) / len(left)
    if busy_right > 0.01 and busy_left > 0.01:
        return ProfileGuess("screen_only", "detected")
    return ProfileGuess(None, "unsure")
