"""Text folding shared by every grouping key (Results merges, word cloud, scale, map, roster order)."""

from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Case-folded without accents, so "Álvaro" and "alvaro" (and "ß"/"ss") are one key."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
