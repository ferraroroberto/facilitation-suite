"""Build ``app/webapp/static/sprite.html`` — this app's trimmed Lucide sprite.

Glyphs present in the vendored ``_vendored/icons/icons-sprite.html`` are copied
from it verbatim; the few it lacks are fetched once from lucide-static (same
version as the vendored sprite, ISC) and pasted verbatim as ``<symbol>``s.
The output is committed; the pages router inlines it into every page at the
``<!--SPRITE-->`` marker (iOS Safari does not resolve external ``<use>``).

Usage:
    .venv/Scripts/python.exe scripts/build_sprite.py
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDORED = ROOT / "app" / "webapp" / "static" / "_vendored" / "icons" / "icons-sprite.html"
OUT = ROOT / "app" / "webapp" / "static" / "sprite.html"
LUCIDE = "https://cdn.jsdelivr.net/npm/lucide-static@1.21.0/icons/{}.svg"

GLYPHS = [
    # nav + header
    "calendar-days", "presentation", "shuffle", "chart-column", "settings", "sun", "moon",
    # common actions
    "plus", "x", "pencil", "trash-2", "copy", "check", "circle-check", "triangle-alert",
    "chevron-right", "chevron-left", "chevron-down", "chevron-up", "ellipsis-vertical", "folder",
    "folder-open", "file-text", "upload", "download", "refresh-cw", "save", "search", "external-link",
    # live
    "play", "pause", "square", "skip-forward", "skip-back", "timer", "clock", "eye", "eye-off",
    "monitor", "message-square", "map", "map-pin", "cloud", "users", "layout-grid", "rows-3",
    "coffee", "image", "keyboard", "camera", "video", "radio", "gauge", "list-ordered",
    "cloud-off", "hard-drive", "grip-vertical", "circle-plus", "sliders-horizontal", "rotate-ccw",
    "smartphone", "text-cursor-input", "type", "wand-sparkles",
]

SYMBOL_RE = re.compile(r'  <symbol id="i-([a-z0-9-]+)" viewBox="0 0 24 24" fill="none">\n(.*?)  </symbol>\n', re.S)


def _from_lucide(name: str) -> str:
    with urllib.request.urlopen(LUCIDE.format(name), timeout=20) as resp:  # noqa: S310 — fixed CDN
        svg = resp.read().decode("utf-8")
    inner = svg.split(">", 2)[2] if svg.startswith("<!--") else svg.split(">", 1)[1]
    inner = inner.rsplit("</svg>", 1)[0].strip("\n")
    lines = [ln.strip() for ln in inner.splitlines() if ln.strip()]
    return "".join(f"    {ln}\n" for ln in lines)


def main() -> int:
    vendored = {m.group(1): m.group(2) for m in SYMBOL_RE.finditer(VENDORED.read_text(encoding="utf-8"))}
    parts = [
        "<!-- facilitation-suite Lucide sprite (built by scripts/build_sprite.py — do not hand-edit).\n"
        "     Glyphs from the vendored _vendored/icons/icons-sprite.html where present, else\n"
        "     pasted verbatim from lucide-static v1.21.0 (ISC). Inline on purpose: iOS Safari\n"
        "     does not resolve external <use> references. -->\n",
        '<svg width="0" height="0" aria-hidden="true" focusable="false"\n'
        '     style="position:absolute;width:0;height:0;overflow:hidden">\n',
    ]
    for name in GLYPHS:
        body = vendored.get(name)
        if body is None:
            body = _from_lucide(name)
            print(f"fetched {name}")
        parts.append(f'  <symbol id="i-{name}" viewBox="0 0 24 24" fill="none">\n{body}  </symbol>\n')
    parts.append("</svg>\n")
    OUT.write_text("".join(parts), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(GLYPHS)} glyphs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
