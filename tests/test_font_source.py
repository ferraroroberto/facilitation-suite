"""Where a stage font value comes from (``static/js/font-source.js``): the global default,
the presentation, or the item — the one resolver the Plan editor, the plan list and
Stage look read. The module has no imports, so Node runs it as it is."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from src.config import PROJECT_ROOT
from src.no_window import NO_WINDOW

MODULE = PROJECT_ROOT / "app" / "webapp" / "static" / "js" / "font-source.js"

# Each case: [session.font, defaults.stage.font, item (or null), prop].
CASES = {
    "global": [{"title_size": 72}, None, {"kind": "activity", "font": {"family": "theme"}}, "size"],
    "presentation": [{"title_size": 90}, None, {"kind": "activity"}, "size"],
    "item": [{"title_size": 90}, {"title_size": 72}, {"kind": "activity", "font": {"size_px": 120}}, "size"],
    "item_matches_global": [{"title_size": 90}, None, {"kind": "activity", "font": {"size_px": 72}}, "size"],
    "slide_has_no_size": [None, None, {"kind": "slide", "font": {"size_px": 120}}, "size"],
    "caps_presentation": [{"caps": False}, None, None, "caps"],
    "caps_item": [None, None, {"kind": "break", "font": {"caps": False}}, "caps"],
    "caps_null_follows": [None, None, {"kind": "break", "font": {"caps": None}}, "caps"],
    "family_global_file": [{"file": "C:/f/Hand.ttf"}, {"file": "C:/f/Hand.ttf"}, {"kind": "activity", "font": {"family": "Patrick Hand"}}, "family"],
    "family_item": [{"family": "Georgia"}, None, {"kind": "activity", "font": {"family": "system-ui"}}, "family"],
}

SCRIPT = """
const m = await import(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = {};
for (const [name, [font, globalFont, item, prop]] of Object.entries(cases)) {
  const r = m.resolveFont({ font }, { stage: { font: globalFont } }, item, prop);
  out[name] = { ...r, label: m.sourceLabel(prop, r) };
}
const it = { kind: 'activity', font: { family: 'theme', size_px: 120 } };
out.own = m.ownLettering({ font: { title_size: 90 } }, null, it);
m.followPresentation(it, 'size');
out.followed = JSON.parse(JSON.stringify(it));
const r = m.resolveFont({ font: { caps: false } }, null, it, 'caps');
m.holdGlobal(it, 'caps', r);
out.held = it;
out.patch = m.globalPatch('family', { stage: { font: { family: 'Georgia' } } });
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def resolved() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed (the gate's static ratchet needs it too)")
    run = subprocess.run([node, "--input-type=module", "-e", SCRIPT, MODULE.as_uri(), json.dumps(CASES)],
                         capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
                         creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_three_sources(resolved: dict) -> None:
    assert resolved["global"] == {"value": 72, "source": "global", "presentation": 72, "global": 72, "label": "Global"}
    assert resolved["presentation"]["source"] == "presentation"
    assert resolved["presentation"]["label"] == "Presentation · global is 72"
    assert resolved["item"] == {"value": 120, "source": "item", "presentation": 90, "global": 72,
                                "label": "This item · presentation is 90"}
    assert resolved["item_matches_global"]["label"] == "This item · matches global"


def test_each_property(resolved: dict) -> None:
    assert resolved["slide_has_no_size"]["source"] == "global"  # a slide keeps its PowerPoint sizes
    assert resolved["caps_presentation"]["label"] == "Presentation · global is ALL CAPS"
    assert resolved["caps_item"]["source"] == "item" and resolved["caps_item"]["value"] is False
    assert resolved["caps_null_follows"]["source"] == "global"
    # the same font file in both layers, and the item's legacy "Patrick Hand" = follow
    assert resolved["family_global_file"]["source"] == "global"
    assert resolved["family_item"]["label"] == "This item · presentation is Georgia (serif)"


def test_resets_and_overrides(resolved: dict) -> None:
    assert resolved["own"] == ["size 120 px (presentation: 90 px)"]
    assert resolved["followed"] == {"kind": "activity", "font": {"family": "theme"}}
    assert resolved["held"]["font"] == {"family": "theme", "caps": True}  # global's ALL CAPS, held by the item
    assert resolved["patch"] == {"file": "", "family": "Georgia"}
