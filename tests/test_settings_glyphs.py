"""The Settings view's help text uses words, not arrow characters (design-review COMP-01, #164)."""

from __future__ import annotations

import re
from pathlib import Path

SETTINGS_JS = Path(__file__).resolve().parents[1] / "app" / "webapp" / "static" / "js" / "views" / "settings.js"

# The rubric's glyph-icon class: arrows (U+2190-21FF), geometric shapes (U+25A0-25FF) and the vertical ellipsis.
GLYPH_ICON = re.compile("[←-⇿■-◿⋮]")


def test_settings_view_has_no_glyph_icons_in_its_text() -> None:
    offenders = [
        f"line {n}: {GLYPH_ICON.findall(line)}"
        for n, line in enumerate(SETTINGS_JS.read_text(encoding="utf-8").splitlines(), 1)
        if GLYPH_ICON.search(line) and not line.lstrip().startswith(("//", "*", "/*"))
    ]
    assert not offenders, "arrow characters in Settings text (use words or a Lucide icon): " + "; ".join(offenders)
