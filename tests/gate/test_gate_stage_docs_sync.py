"""The gate's stage list is written out in `.fleet.toml`, `docs/verification.md` and
`CLAUDE.md`, and each one has drifted from the others before (issue #556): a doc page
named a stale ratchet count, or dropped a ratchet from its "stages in order" paragraph,
every time a new ratchet landed in `.fleet.toml` without a matching doc edit. `.fleet.toml`
is the ground truth -- it is what `scripts/verify-before-ship.ps1` actually runs, per
`tests/test_ratchet_gates.py::test_the_ratchet_is_declared_and_its_script_is_vendored` --
so this test derives the ratchet set from there and fails the moment a doc falls behind it,
instead of waiting for the next audit to notice by hand.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_NUMBER_WORDS = {
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}


def _ratchet_keys() -> list[str]:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    return [key for key in fleet if key.endswith("_ratchet")]


def _slug(key: str) -> str:
    """`doc_refs_ratchet` -> `doc-refs`; matches CLAUDE.md's bolded per-ratchet bullet name."""
    return key[: -len("_ratchet")].replace("_", "-")


def _normalized(text: str) -> str:
    """Collapse `-`/`_` to spaces so `doc-refs ratchet` and `doc refs ratchet` compare equal --
    the docs spell the same ratchet with different separators in different places."""
    return re.sub(r"[-_]", " ", text.lower())


def test_every_fleet_toml_ratchet_has_a_claude_md_bullet() -> None:
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [key for key in _ratchet_keys() if f"**{_slug(key)}**" not in claude_md]
    assert not missing, f"CLAUDE.md's per-ratchet bullet list is missing: {missing}"


def test_claude_md_states_the_true_baseline_count() -> None:
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"Shrink any of the (\w+) baselines", claude_md)
    assert match, "CLAUDE.md should state how many ratchet baselines the gate tracks"
    word = match.group(1).lower()
    assert word in _NUMBER_WORDS, f"unrecognized count word {word!r} -- add it to _NUMBER_WORDS"
    assert _NUMBER_WORDS[word] == len(_ratchet_keys()), (
        f"CLAUDE.md says {word!r} baselines, .fleet.toml declares {len(_ratchet_keys())} "
        "ratchet tables -- update the count (or add the missing bullet) in the same change "
        "a ratchet is added or removed."
    )


def test_every_fleet_toml_ratchet_is_named_in_verification_md() -> None:
    verification_md = _normalized((REPO_ROOT / "docs" / "verification.md").read_text(encoding="utf-8"))
    missing = [
        key for key in _ratchet_keys()
        if _normalized(f"{_slug(key)} ratchet") not in verification_md
    ]
    assert not missing, (
        f"docs/verification.md's stage-order paragraph is missing: {missing} -- it is the "
        "one canonical stage list (README.md and CLAUDE.md point at it instead of "
        "re-enumerating), so every ratchet in .fleet.toml must be named here."
    )


def test_readme_does_not_re_enumerate_the_stage_list() -> None:
    """README.md used to spell out the whole stage chain inline and silently fell behind
    docs/verification.md's. It now just points there -- if a future edit reintroduces a
    full arrow-chained list, this catches it before it can drift again."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    verify_section = readme.split("## Verify", 1)[-1]
    assert verify_section.lower().count("ratchet") <= 1, (
        "README.md's Verify section looks like it re-enumerates the gate's stages; point it "
        "at docs/verification.md instead of listing them here too."
    )
    assert "docs/verification.md" in verify_section
