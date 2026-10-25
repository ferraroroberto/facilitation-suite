"""The quiz's audience-facing words (#91): the stage (``app/activities/quiz/stage.js`` →
``WORDS``) and the phone (``app/player/static/play.js`` → ``TEXT``, plus the ``data-t*``
keys of ``app/player/play.html``) each keep one table per session language. Both languages
must have the same keys (a function the same parameters), and every key the code or the page
asks for must be in the table — a missing one would show ``undefined`` to the audience."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

STAGE = REPO_ROOT / "app" / "activities" / "quiz" / "stage.js"
PHONE = REPO_ROOT / "app" / "player" / "static" / "play.js"
PAGE = REPO_ROOT / "app" / "player" / "play.html"
LANGS = ("en", "es")

# A table entry, one per line at 4 spaces: ``key: 'text',`` or ``key: (a, b) => …``.
ENTRY = re.compile(r"^    (\w+): (?:\(([^)]*)\) =>|(\w+) =>)?", re.M)


def table(path: Path, name: str) -> dict[str, dict[str, tuple[str, ...] | None]]:
    """``{lang: {key: its parameters, or None for a plain value}}`` of ``const <name> = {…}``."""
    code = path.read_text(encoding="utf-8")
    body = code.split(f"const {name} = {{\n", 1)[1].split("\n};", 1)[0]
    out: dict[str, dict[str, tuple[str, ...] | None]] = {}
    for lang in LANGS:
        block = body.split(f"  {lang}: {{\n", 1)[1].split("\n  },", 1)[0]
        out[lang] = {}
        for m in ENTRY.finditer(block):
            params = m.group(2) if m.group(2) is not None else m.group(3)
            out[lang][m.group(1)] = tuple(p.strip() for p in params.split(",") if p.strip()) if params is not None else None
    return out


@pytest.mark.parametrize(("path", "name", "prefix"), [(STAGE, "WORDS", "w"), (PHONE, "TEXT", "T")])
def test_both_languages_have_every_key_the_code_uses(path: Path, name: str, prefix: str) -> None:
    words = table(path, name)
    assert words["en"], f"no English {name} in {path.name}"
    assert set(words["en"]) == set(words["es"]), (
        f"{path.name}: only in en {sorted(set(words['en']) - set(words['es']))}, "
        f"only in es {sorted(set(words['es']) - set(words['en']))}")
    for key, params in words["en"].items():
        assert words["es"][key] == params, (path.name, key)  # the same kind of value, the same parameters
    used = set(re.findall(rf"\b{prefix}\.(\w+)", path.read_text(encoding="utf-8")))
    assert used, f"{path.name} never reads {name}"
    assert used <= set(words["en"]), f"{path.name} asks for missing keys {sorted(used - set(words['en']))}"


def test_the_player_page_words_are_in_the_phone_table() -> None:
    words = table(PHONE, "TEXT")["en"]
    html = PAGE.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-t(?:-placeholder|-label)?="(\w+)"', html))
    assert {"title", "join", "nickname_placeholder", "theme", "removed"} <= keys
    assert keys <= set(words), sorted(keys - set(words))
    assert all(words[k] is None for k in keys)  # the page's words are texts, not functions
